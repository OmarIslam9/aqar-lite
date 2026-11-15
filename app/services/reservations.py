"""منطق الحجز. بدون request ولا commit (الـ route هو صاحب الـ transaction)."""
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Client, Reservation, Unit, Contract
from ..timeutil import utcnow
from . import audit
from . import units as units_service

HOLD = timedelta(hours=72)
UNIQUE_VIOLATION = "23505"


class ReservationError(Exception):
    pass


class ClientNotFound(ReservationError):
    pass


class UnitNotAvailable(ReservationError):
    pass


def _expire(reservation: Reservation, unit: Unit) -> None:
    reservation.status = "expired"
    if unit.status == "reserved":
        units_service.set_status(unit, "available")
    audit.record(company_id=reservation.company_id, actor_id=None,
                 action="reservation_expired", entity="reservation",
                 entity_id=reservation.id, details={"unit_id": unit.id})


def expire_due(company_id: int | None = None) -> int:
    """يفك كل الحجوزات المنتهية. آمن مع الطلبات المتزامنة (SKIP LOCKED).
    ترتيب الأقفال: الوحدة أولاً دائماً، نفس ترتيب reserve_unit، فلا deadlock."""
    stmt = (
        select(Reservation, Unit)
        .join(Unit, Unit.id == Reservation.unit_id)
        .where(Reservation.status == "active", Reservation.expires_at <= utcnow())
        .with_for_update(of=Unit, skip_locked=True)
        .execution_options(populate_existing=True)
    )
    if company_id is not None:
        stmt = stmt.where(Reservation.company_id == company_id)
    pairs = db.session.execute(stmt).all()
    for reservation, unit in pairs:
        _expire(reservation, unit)
    db.session.flush()
    return len(pairs)


def reserve_unit(*, company_id: int, sales_id: int, unit_id: int, client_id: int) -> Reservation:
    # 1) العميل لازم يكون عميل هذا المندوب في نفس الشركة
    client = db.session.execute(
        select(Client).where(Client.id == client_id, Client.company_id == company_id,
                             Client.owner_id == sales_id)
    ).scalar_one_or_none()
    if client is None:
        raise ClientNotFound()

    # 2) قفل صف الوحدة: الطلب المتزامن الثاني ينتظر هنا ثم يرى الحالة الجديدة
    unit = db.session.execute(
        select(Unit).where(Unit.id == unit_id, Unit.company_id == company_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if unit is None:
        raise units_service.UnitNotFound()

    # 3) لو محجوزة بحجز منتهي نفكه أولاً
    if unit.status == "reserved":
        stale = db.session.execute(
            select(Reservation).where(Reservation.unit_id == unit.id,
                                      Reservation.status == "active",
                                      Reservation.expires_at <= utcnow())
            .with_for_update()
        ).scalars().all()
        for old in stale:
            _expire(old, unit)
        db.session.flush()

    if unit.status != "available":
        raise UnitNotAvailable()

    reservation = Reservation(company_id=company_id, unit_id=unit.id, client_id=client.id,
                              sales_id=sales_id, expires_at=utcnow() + HOLD)
    try:
        with db.session.begin_nested():       # savepoint
            db.session.add(reservation)
    except IntegrityError as exc:             # خط الدفاع الأخير: unique index على الحجز النشط
        if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
            raise UnitNotAvailable() from exc
        raise

    units_service.set_status(unit, "reserved")
    audit.record(company_id=company_id, actor_id=sales_id, action="reservation_created",
                 entity="reservation", entity_id=reservation.id,
                 details={"unit_id": unit.id, "client_id": client.id,
                          "expires_at": reservation.expires_at.isoformat()})
    return reservation


def list_reservations(*, company_id: int, role: str, user_id: int, page: int,
                      per_page: int, client_id: int | None = None):
    conds = [Reservation.company_id == company_id]
    if role == "sales":
        conds.append(Reservation.sales_id == user_id)
    if client_id is not None:
        conds.append(Reservation.client_id == client_id)
    total = db.session.execute(select(func.count(Reservation.id)).where(*conds)).scalar_one()
    rows = db.session.execute(
        select(Reservation, Unit, Client, Contract.id)
        .select_from(Reservation)
        .join(Unit, Unit.id == Reservation.unit_id)
        .join(Client, Client.id == Reservation.client_id)
        .outerjoin(Contract, Contract.reservation_id == Reservation.id)
        .where(*conds).order_by(Reservation.id.desc())
        .limit(per_page).offset((page - 1) * per_page)
    ).all()
    return rows, total