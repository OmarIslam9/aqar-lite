import threading
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models import AuditLog, Contract, Installment, Reservation, Unit, User
from app.services import contracts as contracts_service
from app.services.schedule import min_final_price
from app.timeutil import utcnow
from tests.factories import (accountant_id, contract_input, make_draft,
                             make_reservation)


def _approve(d: dict, acc: int):
    contracts_service.approve_contract(company_id=d["company_id"], accountant_id=acc,
                                       contract_id=d["contract_id"])


def _installments(contract_id: int):
    return db.session.execute(select(Installment).where(Installment.contract_id == contract_id)
                              .order_by(Installment.seq)).scalars().all()


def test_approval_generates_exact_schedule_and_sells_unit(app):
    with app.app_context():
        d = make_draft()
        acc = accountant_id()
        _approve(d, acc)
        db.session.commit()

        contract = db.session.get(Contract, d["contract_id"])
        assert contract.status == "approved" and contract.approved_by == acc
        assert db.session.get(Unit, d["unit_id"]).status == "sold"
        assert db.session.get(Reservation, d["reservation_id"]).status == "converted"

        rows = _installments(d["contract_id"])
        assert [r.seq for r in rows] == list(range(0, d["count"] + 1))
        assert sum(r.amount_minor for r in rows) == d["final_minor"]
        base, extra = divmod(d["final_minor"] - d["down_minor"], d["count"])
        assert extra > 0                                  # الاختبار فعلاً يغطي باقي القسمة
        assert rows[0].amount_minor == d["down_minor"]
        assert all(r.amount_minor == base for r in rows[1:-1])
        assert rows[-1].amount_minor == base + extra
        assert all(r.paid_minor == 0 for r in rows)

        audited = db.session.execute(select(func.count()).select_from(AuditLog)
                                     .where(AuditLog.action == "contract_approved")).scalar_one()
        assert audited == 1


def test_second_approval_is_rejected_without_duplicating_schedule(app):
    with app.app_context():
        d = make_draft()
        acc = accountant_id()
        _approve(d, acc)
        db.session.commit()
        with pytest.raises(contracts_service.AlreadyApproved):
            _approve(d, acc)
        db.session.rollback()
        assert len(_installments(d["contract_id"])) == d["count"] + 1


def test_concurrent_approval_only_one_wins(app):
    with app.app_context():
        d = make_draft()
        acc = accountant_id()
    n = 4
    barrier = threading.Barrier(n)
    results: list[str] = []

    def attempt():
        with app.app_context():
            try:
                barrier.wait(timeout=10)
                _approve(d, acc)
                db.session.commit()
                results.append("ok")
            except contracts_service.AlreadyApproved:
                db.session.rollback()
                results.append("already")
            except Exception as exc:
                db.session.rollback()
                results.append(f"error: {exc!r}")

    threads = [threading.Thread(target=attempt) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1 and results.count("already") == n - 1, results
    with app.app_context():
        assert len(_installments(d["contract_id"])) == d["count"] + 1


def test_discount_over_five_percent_is_rejected(app):
    with app.app_context():
        ctx = make_reservation()
        too_low = min_final_price(ctx["list_minor"]) - 1
        with pytest.raises(contracts_service.InvalidTerms) as exc:
            contracts_service.create_contract(company_id=ctx["company_id"], sales_id=ctx["sales_id"],
                                              data=contract_input(ctx, final_minor=too_low))
        assert "final_price" in exc.value.errors
        db.session.rollback()

        contracts_service.create_contract(                 # الحد الأدنى بالظبط مقبول
            company_id=ctx["company_id"], sales_id=ctx["sales_id"],
            data=contract_input(ctx, final_minor=min_final_price(ctx["list_minor"])))
        db.session.commit()


def test_cannot_create_contract_from_another_salesmans_reservation(app):
    with app.app_context():
        ctx = make_reservation()
        other = db.session.execute(select(User).where(User.email == "sales2@nakheel.test")).scalar_one()
        with pytest.raises(contracts_service.ReservationNotFound):
            contracts_service.create_contract(company_id=ctx["company_id"], sales_id=other.id,
                                              data=contract_input(ctx, final_minor=ctx["list_minor"]))


def test_expired_reservation_blocks_contract_creation(app):
    with app.app_context():
        ctx = make_reservation()
        db.session.execute(update(Reservation).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.session.commit()
        with pytest.raises(contracts_service.ReservationNotActive):
            contracts_service.create_contract(company_id=ctx["company_id"], sales_id=ctx["sales_id"],
                                              data=contract_input(ctx, final_minor=ctx["list_minor"]))
        db.session.rollback()


def test_expired_reservation_blocks_approval(app):
    with app.app_context():
        d = make_draft()
        db.session.execute(update(Reservation).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.session.commit()
        with pytest.raises(contracts_service.ReservationNotActive):
            _approve(d, accountant_id())
        db.session.rollback()
        assert db.session.get(Contract, d["contract_id"]).status == "draft"
        assert _installments(d["contract_id"]) == []


def test_db_blocks_editing_approved_contract_and_schedule(app):
    with app.app_context():
        d = make_draft()
        _approve(d, accountant_id())
        db.session.commit()
        for sql in ("UPDATE contracts SET final_price_minor = final_price_minor - 1",
                    "DELETE FROM contracts",
                    "UPDATE installments SET amount_minor = amount_minor + 1",
                    "UPDATE installments SET due_date = due_date + 1",
                    "DELETE FROM installments"):
            with pytest.raises(DBAPIError):
                db.session.execute(text(sql))
            db.session.rollback()


def test_db_refuses_approval_without_matching_schedule(app):
    with app.app_context():
        d = make_draft()
        db.session.execute(
            text("UPDATE contracts SET status='approved', approved_by=:a, approved_at=now() WHERE id=:i"),
            {"a": accountant_id(), "i": d["contract_id"]})
        with pytest.raises(DBAPIError):          # الفحص المؤجَّل يفشل عند الـ commit
            db.session.commit()
        db.session.rollback()