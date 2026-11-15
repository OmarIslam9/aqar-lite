from decimal import Decimal

from sqlalchemy import select

from app.extensions import db
from app.models import Client, Company, Unit, User
from app.schemas import ContractIn
from app.services import contracts as contracts_service
from app.services import reservations as reservations_service
from app.timeutil import today_local


def minor_to_decimal(minor: int) -> Decimal:
    return Decimal(minor).scaleb(-2)


def company_id(name_part: str = "النخيل") -> int:
    return db.session.execute(
        select(Company.id).where(Company.name.like(f"%{name_part}%"))).scalar_one()


def accountant_id(email: str = "accountant1@nakheel.test") -> int:
    return db.session.execute(select(User.id).where(User.email == email)).scalar_one()


def make_reservation(company_part="النخيل", sales_email="sales1@nakheel.test", unit_offset=0) -> dict:
    """يحجز وحدة لأول عميل عند المندوب ويرجّع المعرّفات. يُستدعى داخل app_context."""
    cid = company_id(company_part)
    sales = db.session.execute(select(User).where(User.email == sales_email)).scalar_one()
    client = db.session.execute(select(Client).where(Client.owner_id == sales.id)
                                .order_by(Client.id)).scalars().first()
    unit = db.session.execute(select(Unit).where(Unit.company_id == cid)
                              .order_by(Unit.id).offset(unit_offset)).scalars().first()
    sales_id, unit_id, list_minor = sales.id, unit.id, unit.price_minor
    reservation = reservations_service.reserve_unit(
        company_id=cid, sales_id=sales_id, unit_id=unit_id, client_id=client.id)
    reservation_id = reservation.id
    db.session.commit()
    return {"company_id": cid, "sales_id": sales_id, "unit_id": unit_id,
            "reservation_id": reservation_id, "list_minor": list_minor}


def contract_input(ctx: dict, *, final_minor: int, down_minor: int = 0, count: int = 12) -> ContractIn:
    return ContractIn(reservation_id=ctx["reservation_id"],
                      final_price=minor_to_decimal(final_minor),
                      down_payment=minor_to_decimal(down_minor),
                      installments_count=count, start_date=today_local())


def make_draft(*, final_pct=97, down_minor=10_000_000, count=7, **kwargs) -> dict:
    ctx = make_reservation(**kwargs)
    final_minor = ctx["list_minor"] * final_pct // 100
    contract = contracts_service.create_contract(
        company_id=ctx["company_id"], sales_id=ctx["sales_id"],
        data=contract_input(ctx, final_minor=final_minor, down_minor=down_minor, count=count))
    contract_id = contract.id
    db.session.commit()
    return {**ctx, "contract_id": contract_id, "final_minor": final_minor,
            "down_minor": down_minor, "count": count}