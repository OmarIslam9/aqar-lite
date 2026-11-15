import threading
from datetime import timedelta

from sqlalchemy import select, update

from app.extensions import db
from app.models import Client, Company, Reservation, Unit, User
from app.services import reservations as svc
from app.timeutil import utcnow


def _company_id(name_part: str) -> int:
    return db.session.execute(
        select(Company.id).where(Company.name.like(f"%{name_part}%"))).scalar_one()


def test_concurrent_reservation_only_one_wins(app):
    with app.app_context():
        company_id = _company_id("النخيل")
        unit_id = db.session.execute(
            select(Unit.id).where(Unit.company_id == company_id).order_by(Unit.id)).scalars().first()
        attempts = [(c.owner_id, c.id) for c in db.session.execute(
            select(Client).where(Client.company_id == company_id)).scalars().all()]
    assert len(attempts) >= 6

    barrier = threading.Barrier(len(attempts))
    results: list[str] = []

    def attempt(sales_id: int, client_id: int):
        with app.app_context():
            try:
                barrier.wait(timeout=10)
                svc.reserve_unit(company_id=company_id, sales_id=sales_id,
                                 unit_id=unit_id, client_id=client_id)
                db.session.commit()
                results.append("ok")
            except svc.UnitNotAvailable:
                db.session.rollback()
                results.append("taken")
            except Exception as exc:  # أي خطأ آخر يفشل الاختبار بوضوح
                db.session.rollback()
                results.append(f"error: {exc!r}")

    threads = [threading.Thread(target=attempt, args=a) for a in attempts]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1, results
    assert results.count("taken") == len(attempts) - 1, results
    with app.app_context():
        assert db.session.get(Unit, unit_id).status == "reserved"
        active = db.session.execute(select(Reservation).where(
            Reservation.unit_id == unit_id, Reservation.status == "active")).scalars().all()
        assert len(active) == 1


def test_expired_reservation_releases_unit_and_allows_new_one(app):
    with app.app_context():
        company_id = _company_id("النخيل")
        unit = db.session.execute(select(Unit).where(Unit.company_id == company_id)).scalars().first()
        client = db.session.execute(select(Client).where(Client.company_id == company_id)).scalars().first()
        res = svc.reserve_unit(company_id=company_id, sales_id=client.owner_id,
                               unit_id=unit.id, client_id=client.id)
        db.session.commit()
        assert (res.expires_at - utcnow()) > timedelta(hours=71)

        db.session.execute(update(Reservation).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.session.commit()

        assert svc.expire_due(company_id) == 1
        db.session.commit()
        assert db.session.get(Unit, unit.id).status == "available"

        svc.reserve_unit(company_id=company_id, sales_id=client.owner_id,
                         unit_id=unit.id, client_id=client.id)
        db.session.commit()
        assert db.session.get(Unit, unit.id).status == "reserved"


def test_cannot_reserve_with_another_salesmans_client(app):
    with app.app_context():
        company_id = _company_id("النخيل")
        unit = db.session.execute(select(Unit).where(Unit.company_id == company_id)).scalars().first()
        sales = db.session.execute(select(User).where(User.company_id == company_id,
                                                      User.role == "sales").order_by(User.id)).scalars().all()
        other_client = db.session.execute(select(Client).where(Client.owner_id == sales[1].id)).scalars().first()
        try:
            svc.reserve_unit(company_id=company_id, sales_id=sales[0].id,
                             unit_id=unit.id, client_id=other_client.id)
            assert False, "should have raised"
        except svc.ClientNotFound:
            db.session.rollback()