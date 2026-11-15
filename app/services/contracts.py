"""منطق العقود. بدون request ولا commit (الـ route هو صاحب الـ transaction).
ترتيب الأقفال دائماً: الوحدة ← العقد ← الحجز (نفس ترتيب الحجز والانتهاء، فلا deadlock)."""
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Client, Contract, Installment, Reservation, Unit, User
from ..money import to_minor
from ..schemas import ContractIn
from ..timeutil import utcnow
from . import audit
from . import units as units_service
from .schedule import build_schedule, check_terms, installment_status

UNIQUE_VIOLATION = "23505"


class ContractError(Exception):
    pass


class ReservationNotFound(ContractError):
    pass


class ReservationNotActive(ContractError):
    pass


class ContractExists(ContractError):
    pass


class ContractNotFound(ContractError):
    pass


class AlreadyApproved(ContractError):
    pass


class InvalidTerms(ContractError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("invalid contract terms")
        self.errors = errors


@dataclass(frozen=True)
class ScheduleRow:
    seq: int
    due_date: date
    amount_minor: int
    paid_minor: int
    status: str


@dataclass(frozen=True)
class Totals:
    paid_minor: int
    remaining_minor: int


# ---------- أقفال ----------
def _lock_unit(company_id: int, unit_id: int) -> Unit:
    unit = db.session.execute(
        select(Unit).where(Unit.id == unit_id, Unit.company_id == company_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if unit is None:
        raise units_service.UnitNotFound()
    return unit


def _lock_reservation(company_id: int, reservation_id: int) -> Reservation:
    reservation = db.session.execute(
        select(Reservation).where(Reservation.id == reservation_id,
                                  Reservation.company_id == company_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if reservation is None:
        raise ReservationNotFound()
    return reservation


# ---------- قراءة ----------
def get_reservation_for_contract(*, company_id: int, sales_id: int, reservation_id: int):
    """حجز هذا المندوب فقط. يرجّع (Reservation, Unit, Client, contract_id أو None)."""
    row = db.session.execute(
        select(Reservation, Unit, Client, Contract.id)
        .select_from(Reservation)
        .join(Unit, Unit.id == Reservation.unit_id)
        .join(Client, Client.id == Reservation.client_id)
        .outerjoin(Contract, Contract.reservation_id == Reservation.id)
        .where(Reservation.id == reservation_id, Reservation.company_id == company_id,
               Reservation.sales_id == sales_id)
    ).first()
    if row is None:
        raise ReservationNotFound()
    return row


def list_contracts(*, company_id: int, role: str, user_id: int, page: int, per_page: int):
    conds = [Contract.company_id == company_id]
    if role == "sales":
        conds.append(Contract.sales_id == user_id)
    total = db.session.execute(select(func.count(Contract.id)).where(*conds)).scalar_one()
    rows = db.session.execute(
        select(Contract, Unit, Client, User.full_name)
        .join(Unit, Unit.id == Contract.unit_id)
        .join(Client, Client.id == Contract.client_id)
        .join(User, User.id == Contract.sales_id)
        .where(*conds).order_by(Contract.id.desc())
        .limit(per_page).offset((page - 1) * per_page)
    ).all()
    return rows, total


def get_contract(*, company_id: int, role: str, user_id: int, contract_id: int):
    """يرجّع (Contract, Unit, Client, sales_name, Reservation). عقد غيرك = 404."""
    conds = [Contract.company_id == company_id, Contract.id == contract_id]
    if role == "sales":
        conds.append(Contract.sales_id == user_id)
    row = db.session.execute(
        select(Contract, Unit, Client, User.full_name, Reservation)
        .join(Unit, Unit.id == Contract.unit_id)
        .join(Client, Client.id == Contract.client_id)
        .join(User, User.id == Contract.sales_id)
        .join(Reservation, Reservation.id == Contract.reservation_id)
        .where(*conds)
    ).first()
    if row is None:
        raise ContractNotFound()
    return row


def schedule_rows(contract: Contract, today: date) -> list[ScheduleRow]:
    """المعتمد: من جدول installments. المسودة: معاينة محسوبة (لا تُخزَّن)."""
    if contract.status == "approved":
        installments = db.session.execute(
            select(Installment)
            .where(Installment.company_id == contract.company_id,
                   Installment.contract_id == contract.id)
            .order_by(Installment.seq)
        ).scalars().all()
        return [ScheduleRow(i.seq, i.due_date, i.amount_minor, i.paid_minor,
                            installment_status(amount_minor=i.amount_minor, paid_minor=i.paid_minor,
                                               due_date=i.due_date, today=today))
                for i in installments]
    items = build_schedule(final_minor=contract.final_price_minor,
                           down_minor=contract.down_payment_minor,
                           count=contract.installments_count, start_date=contract.start_date)
    return [ScheduleRow(i.seq, i.due_date, i.amount_minor, 0, "pending") for i in items]


def totals(rows: list[ScheduleRow]) -> Totals:
    return Totals(paid_minor=sum(r.paid_minor for r in rows),
                  remaining_minor=sum(r.amount_minor - r.paid_minor for r in rows))


# ---------- كتابة ----------
def create_contract(*, company_id: int, sales_id: int, data: ContractIn) -> Contract:
    # الحجز لازم يخص هذا المندوب ونفس الشركة (قراءة بدون قفل لمعرفة الوحدة)
    unit_id = db.session.execute(
        select(Reservation.unit_id).where(
            Reservation.id == data.reservation_id, Reservation.company_id == company_id,
            Reservation.sales_id == sales_id)
    ).scalar_one_or_none()
    if unit_id is None:
        raise ReservationNotFound()

    unit = _lock_unit(company_id, unit_id)
    reservation = _lock_reservation(company_id, data.reservation_id)
    if reservation.sales_id != sales_id:
        raise ReservationNotFound()
    if (reservation.status != "active" or reservation.expires_at <= utcnow()
            or unit.status != "reserved"):
        raise ReservationNotActive()

    list_minor = unit.price_minor          # السعر الأصلي من الخادم، لا من الطلب
    final_minor = to_minor(data.final_price)
    down_minor = to_minor(data.down_payment)
    errors = check_terms(list_minor=list_minor, final_minor=final_minor,
                         down_minor=down_minor, count=data.installments_count)
    if errors:
        raise InvalidTerms(errors)

    contract = Contract(
        company_id=company_id, unit_id=unit.id, client_id=reservation.client_id,
        reservation_id=reservation.id, sales_id=sales_id, list_price_minor=list_minor,
        final_price_minor=final_minor, down_payment_minor=down_minor,
        installments_count=data.installments_count, start_date=data.start_date, status="draft")
    try:
        with db.session.begin_nested():    # savepoint: reservation_id فريد
            db.session.add(contract)
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
            raise ContractExists() from exc
        raise

    audit.record(company_id=company_id, actor_id=sales_id, action="contract_created",
                 entity="contract", entity_id=contract.id,
                 details={"unit_id": unit.id, "list_price_minor": list_minor,
                          "final_price_minor": final_minor})
    return contract


def approve_contract(*, company_id: int, accountant_id: int, contract_id: int) -> Contract:
    """كل شيء داخل transaction واحدة (الـ route يعمل commit): لو فشل أي جزء لا يبقى شيء نصف منفّذ."""
    unit_id = db.session.execute(
        select(Contract.unit_id).where(Contract.id == contract_id,
                                       Contract.company_id == company_id)
    ).scalar_one_or_none()
    if unit_id is None:
        raise ContractNotFound()

    unit = _lock_unit(company_id, unit_id)
    contract = db.session.execute(
        select(Contract).where(Contract.id == contract_id, Contract.company_id == company_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one()
    if contract.status != "draft":
        raise AlreadyApproved()          # ضغطتين على "اعتماد" أو اعتماد متزامن

    reservation = _lock_reservation(company_id, contract.reservation_id)
    if (reservation.status != "active" or reservation.expires_at <= utcnow()
            or unit.status != "reserved"):
        raise ReservationNotActive()

    contract.status = "approved"
    contract.approved_by = accountant_id
    contract.approved_at = utcnow()
    reservation.status = "converted"
    units_service.set_status(unit, "sold")

    items = build_schedule(final_minor=contract.final_price_minor,
                           down_minor=contract.down_payment_minor,
                           count=contract.installments_count, start_date=contract.start_date)
    db.session.add_all([
        Installment(company_id=company_id, contract_id=contract.id, seq=i.seq,
                    due_date=i.due_date, amount_minor=i.amount_minor)
        for i in items])

    audit.record(company_id=company_id, actor_id=accountant_id, action="contract_approved",
                 entity="contract", entity_id=contract.id,
                 details={"unit_id": unit.id, "final_price_minor": contract.final_price_minor,
                          "down_payment_minor": contract.down_payment_minor,
                          "installments_count": contract.installments_count,
                          "start_date": contract.start_date.isoformat()})
    db.session.flush()
    return contract