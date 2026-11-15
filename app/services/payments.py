"""منطق الدفعات. بدون request ولا commit (الـ route هو صاحب الـ transaction).
ترتيب الأقفال: العقد ثم أقساطه (بترتيب seq)، فلا deadlock بين دفعتين متزامنتين."""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Contract, Installment, Payment, PaymentAllocation, User
from . import audit
from .allocation import Open, allocate, remaining_total

UNIQUE_VIOLATION = "23505"


class PaymentError(Exception):
    pass


class ContractNotFound(PaymentError):
    pass


class ContractNotApproved(PaymentError):
    pass


class KeyConflict(PaymentError):
    """نفس المفتاح استُخدم لدفعة مختلفة (عقد أو مبلغ مختلف)."""


class Overpayment(PaymentError):
    def __init__(self, remaining_minor: int):
        super().__init__("payment exceeds the remaining contract balance")
        self.remaining_minor = remaining_minor


@dataclass(frozen=True)
class PaymentResult:
    payment: Payment
    duplicate: bool      # True = المفتاح مسجَّل من قبل، ولم تُنشأ دفعة جديدة


def record_payment(*, company_id: int, accountant_id: int, contract_id: int,
                   amount_minor: int, idempotency_key: str, ip: str | None = None) -> PaymentResult:
    # 1) قفل العقد: أي دفعة متزامنة على نفس العقد تنتظر هنا
    contract = db.session.execute(
        select(Contract).where(Contract.id == contract_id, Contract.company_id == company_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if contract is None:
        raise ContractNotFound()

    # 2) إعادة إرسال بنفس المفتاح: نرجّع الدفعة الأصلية ولا ننشئ ثانية
    existing = db.session.execute(
        select(Payment).where(Payment.company_id == company_id,
                              Payment.idempotency_key == idempotency_key)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.contract_id != contract.id or existing.amount_minor != amount_minor:
            raise KeyConflict()
        return PaymentResult(existing, True)

    if contract.status != "approved":
        raise ContractNotApproved()

    # 3) قفل الأقساط وتوزيع المبلغ
    installments = db.session.execute(
        select(Installment)
        .where(Installment.contract_id == contract.id, Installment.company_id == company_id)
        .order_by(Installment.seq)
        .with_for_update().execution_options(populate_existing=True)
    ).scalars().all()
    items = [Open(i.id, i.seq, i.amount_minor, i.paid_minor) for i in installments]
    remaining = remaining_total(items)
    if amount_minor > remaining:
        raise Overpayment(remaining)
    plan = allocate(amount_minor, items)

    payment = Payment(company_id=company_id, contract_id=contract.id, amount_minor=amount_minor,
                      idempotency_key=idempotency_key, received_by=accountant_id)
    try:
        with db.session.begin_nested():      # savepoint: المفتاح فريد لكل شركة
            db.session.add(payment)
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
            raise KeyConflict() from exc
        raise

    by_id = {i.id: i for i in installments}
    for installment_id, applied in plan:
        db.session.add(PaymentAllocation(payment_id=payment.id, installment_id=installment_id,
                                         amount_minor=applied))
        by_id[installment_id].paid_minor += applied

    audit.record(company_id=company_id, actor_id=accountant_id, action="payment_recorded",
                 entity="payment", entity_id=payment.id, ip=ip,
                 details={"contract_id": contract.id, "amount_minor": amount_minor,
                          "idempotency_key": idempotency_key,
                          "allocations": [{"seq": by_id[i].seq, "amount_minor": a}
                                          for i, a in plan]})
    db.session.flush()
    return PaymentResult(payment, False)


def history(*, company_id: int, contract_id: int):
    """يرجّع [(Payment, اسم المستلم, [(seq, المبلغ)])] الأحدث أولاً."""
    rows = db.session.execute(
        select(Payment, User.full_name)
        .join(User, User.id == Payment.received_by)
        .where(Payment.company_id == company_id, Payment.contract_id == contract_id)
        .order_by(Payment.id.desc())
    ).all()
    allocations: dict[int, list[tuple[int, int]]] = {}
    ids = [p.id for p, _ in rows]
    if ids:
        for payment_id, seq, amount in db.session.execute(
            select(PaymentAllocation.payment_id, Installment.seq, PaymentAllocation.amount_minor)
            .join(Installment, Installment.id == PaymentAllocation.installment_id)
            .where(PaymentAllocation.payment_id.in_(ids))
            .order_by(Installment.seq)
        ):
            allocations.setdefault(payment_id, []).append((seq, amount))
    return [(p, name, allocations.get(p.id, [])) for p, name in rows]