"""منطق الوحدات. لا يعتمد على request، ولا يعمل commit (الـ route هو صاحب الـ transaction)."""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Unit
from ..money import to_minor
from ..schemas import UnitIn, UnitQuery
from . import audit

ALLOWED_TRANSITIONS = frozenset({
    ("available", "reserved"),   # عند الحجز
    ("reserved", "available"),   # عند انتهاء الحجز
    ("reserved", "sold"),        # عند اعتماد العقد
})
UNIQUE_VIOLATION = "23505"

SORTS = {
    "price": Unit.price_minor.asc(),
    "-price": Unit.price_minor.desc(),
    "area": Unit.area_sqm.asc(),
    "-area": Unit.area_sqm.desc(),
}


class UnitError(Exception):
    pass


class UnitNotFound(UnitError):
    pass


class DuplicateUnit(UnitError):
    pass


class PriceLocked(UnitError):
    pass


class InvalidTransition(UnitError):
    pass


def check_transition(current: str, new: str) -> None:
    if (current, new) not in ALLOWED_TRANSITIONS:
        raise InvalidTransition(f"{current} -> {new}")


def set_status(unit: Unit, new_status: str) -> None:
    """المكان الوحيد الذي تتغير فيه حالة الوحدة (الحجز والعقود يستخدمانه لاحقاً)."""
    check_transition(unit.status, new_status)
    unit.status = new_status


def list_units(company_id: int, q: UnitQuery) -> tuple[list[Unit], int]:
    stmt = select(Unit).where(Unit.company_id == company_id)
    if q.status:
        stmt = stmt.where(Unit.status == q.status)
    if q.min_price is not None:
        stmt = stmt.where(Unit.price_minor >= to_minor(q.min_price))
    if q.max_price is not None:
        stmt = stmt.where(Unit.price_minor <= to_minor(q.max_price))

    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.session.execute(
        stmt.order_by(SORTS[q.sort], Unit.id)
        .limit(q.per_page).offset((q.page - 1) * q.per_page)
    ).scalars().all()
    return list(rows), total


def _flush_or_duplicate() -> None:
    """flush داخل savepoint: التكرار يرجّع DuplicateUnit، وأي خطأ آخر يطلع كما هو."""
    try:
        with db.session.begin_nested():
            pass
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
            raise DuplicateUnit() from exc
        raise


def create_unit(*, company_id: int, actor_id: int, data: UnitIn) -> Unit:
    unit = Unit(company_id=company_id, project=data.project, number=data.number,
                type=data.type, area_sqm=data.area_sqm, rooms=data.rooms,
                price_minor=to_minor(data.price), status="available")
    try:
        with db.session.begin_nested():
            db.session.add(unit)
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
            raise DuplicateUnit() from exc
        raise
    audit.record(company_id=company_id, actor_id=actor_id, action="unit_created",
                 entity="unit", entity_id=unit.id,
                 details={"project": unit.project, "number": unit.number,
                          "price_minor": unit.price_minor})
    return unit


def update_unit(*, company_id: int, actor_id: int, unit_id: int, data: UnitIn) -> Unit:
    # قفل الصف: يمنع تعديل السعر أثناء حجز متزامن لنفس الوحدة
    unit = db.session.execute(
        select(Unit)
        .where(Unit.id == unit_id, Unit.company_id == company_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if unit is None:
        raise UnitNotFound()

    new_price = to_minor(data.price)
    old_price = unit.price_minor
    if new_price != old_price and unit.status != "available":
        raise PriceLocked()

    unit.project, unit.number, unit.type = data.project, data.number, data.type
    unit.area_sqm, unit.rooms, unit.price_minor = data.area_sqm, data.rooms, new_price
    try:
        with db.session.begin_nested():
            db.session.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
            raise DuplicateUnit() from exc
        raise

    if new_price != old_price:
        audit.record(company_id=company_id, actor_id=actor_id, action="unit_price_changed",
                     entity="unit", entity_id=unit.id,
                     details={"number": unit.number, "old_price_minor": old_price,
                              "new_price_minor": new_price})
    return unit