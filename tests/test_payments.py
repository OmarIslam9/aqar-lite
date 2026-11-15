import threading
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models import AuditLog, Contract, Installment, Payment, PaymentAllocation
from app.services import contracts as contracts_service
from app.services import payments as svc
from app.timeutil import today_local
from tests.factories import accountant_id, company_id, make_approved, make_draft


def _pay(d, amount, key=None, acc=None, cid=None):
    return svc.record_payment(company_id=cid or d["company_id"], accountant_id=acc or accountant_id(),
                              contract_id=d["contract_id"], amount_minor=amount,
                              idempotency_key=key or uuid.uuid4().hex)


def _rows(contract_id):
    return db.session.execute(select(Installment).where(Installment.contract_id == contract_id)
                              .order_by(Installment.seq)).scalars().all()


def _count(model) -> int:
    return db.session.execute(select(func.count()).select_from(model)).scalar_one()


def _run_parallel(app, n, work):
    barrier = threading.Barrier(n)
    results: list[str] = []

    def runner(i):
        with app.app_context():
            try:
                barrier.wait(timeout=10)
                out = work(i)
                db.session.commit()
                results.append(out)
            except svc.Overpayment:
                db.session.rollback()
                results.append("overpay")
            except Exception as exc:
                db.session.rollback()
                results.append(f"error: {exc!r}")

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_payment_goes_to_oldest_installments_first(app):
    with app.app_context():
        d = make_approved()
        amount = d["down_minor"] + 5_000_000
        result = _pay(d, amount)
        db.session.commit()
        assert result.duplicate is False

        rows = _rows(d["contract_id"])
        assert rows[0].paid_minor == rows[0].amount_minor == d["down_minor"]
        assert rows[1].paid_minor == 5_000_000 < rows[1].amount_minor
        assert all(r.paid_minor == 0 for r in rows[2:])

        contract = db.session.get(Contract, d["contract_id"])
        statuses = [r.status for r in contracts_service.schedule_rows(contract, today_local())]
        assert statuses[:2] == ["paid", "partial"] and set(statuses[2:]) == {"pending"}

        allocated = db.session.execute(select(func.sum(PaymentAllocation.amount_minor))).scalar_one()
        assert allocated == amount
        logged = db.session.execute(select(AuditLog).where(AuditLog.action == "payment_recorded")).scalars().all()
        assert len(logged) == 1 and logged[0].details["amount_minor"] == amount


def test_second_payment_continues_from_partial_installment(app):
    with app.app_context():
        d = make_approved()
        _pay(d, d["down_minor"] + 5_000_000)
        db.session.commit()
        rows = _rows(d["contract_id"])
        _pay(d, (rows[1].amount_minor - rows[1].paid_minor) + 1)
        db.session.commit()
        rows = _rows(d["contract_id"])
        assert rows[1].paid_minor == rows[1].amount_minor
        assert rows[2].paid_minor == 1


def test_overdue_status_after_due_date(app):
    with app.app_context():
        d = make_approved()
        _pay(d, d["down_minor"])
        db.session.commit()
        contract = db.session.get(Contract, d["contract_id"])
        statuses = [r.status for r in contracts_service.schedule_rows(contract, date(2100, 1, 1))]
        assert statuses[0] == "paid" and set(statuses[1:]) == {"overdue"}


def test_overpayment_is_rejected_and_nothing_is_written(app):
    with app.app_context():
        d = make_approved()
        with pytest.raises(svc.Overpayment) as exc:
            _pay(d, d["final_minor"] + 1)
        db.session.rollback()
        assert exc.value.remaining_minor == d["final_minor"]
        assert _count(Payment) == 0 and _count(PaymentAllocation) == 0
        assert all(r.paid_minor == 0 for r in _rows(d["contract_id"]))


def test_full_payment_then_any_extra_is_rejected(app):
    with app.app_context():
        d = make_approved()
        _pay(d, d["final_minor"])
        db.session.commit()
        assert all(r.paid_minor == r.amount_minor for r in _rows(d["contract_id"]))
        with pytest.raises(svc.Overpayment) as exc:
            _pay(d, 1)
        db.session.rollback()
        assert exc.value.remaining_minor == 0


def test_retry_with_same_key_does_not_duplicate(app):
    with app.app_context():
        d = make_approved()
        key = uuid.uuid4().hex
        first = _pay(d, 2_000_000, key=key)
        db.session.commit()
        first_id = first.payment.id
        second = _pay(d, 2_000_000, key=key)
        db.session.commit()
        assert second.duplicate is True and second.payment.id == first_id
        assert _count(Payment) == 1
        assert sum(r.paid_minor for r in _rows(d["contract_id"])) == 2_000_000


def test_same_key_with_different_payload_is_a_conflict(app):
    with app.app_context():
        d = make_approved()
        key = uuid.uuid4().hex
        _pay(d, 2_000_000, key=key)
        db.session.commit()
        with pytest.raises(svc.KeyConflict):
            _pay(d, 2_000_001, key=key)
        db.session.rollback()

        other = make_approved(unit_offset=1)           # عقد تاني بنفس المفتاح
        with pytest.raises(svc.KeyConflict):
            _pay(other, 2_000_000, key=key)
        db.session.rollback()
        assert _count(Payment) == 1


def test_draft_contract_cannot_receive_payments(app):
    with app.app_context():
        d = make_draft()
        with pytest.raises(svc.ContractNotApproved):
            _pay(d, 100)
        db.session.rollback()
        assert _count(Payment) == 0


def test_payment_on_another_companys_contract_is_not_found(app):
    with app.app_context():
        d = make_approved()
        with pytest.raises(svc.ContractNotFound):
            _pay(d, 100, cid=company_id("الواحة"))
        db.session.rollback()
        assert _count(Payment) == 0


def test_concurrent_payments_never_exceed_remaining(app):
    with app.app_context():
        d = make_approved()
        acc = accountant_id()
    amount = d["final_minor"] * 40 // 100          # دفعتين 80% تنجحوا، والتالتة 120% لازم تترفض
    results = _run_parallel(app, 5, lambda i: "created" if not _pay(d, amount, acc=acc).duplicate else "dup")
    assert results.count("created") == 2 and results.count("overpay") == 3, results
    with app.app_context():
        rows = _rows(d["contract_id"])
        assert sum(r.paid_minor for r in rows) == 2 * amount
        assert all(r.paid_minor <= r.amount_minor for r in rows)
        assert _count(Payment) == 2


def test_concurrent_retries_with_same_key_create_one_payment(app):
    with app.app_context():
        d = make_approved()
        acc = accountant_id()
    key = uuid.uuid4().hex
    results = _run_parallel(app, 5, lambda i: "dup" if _pay(d, 3_000_000, key=key, acc=acc).duplicate else "created")
    assert results.count("created") == 1 and results.count("dup") == 4, results
    with app.app_context():
        assert _count(Payment) == 1
        assert sum(r.paid_minor for r in _rows(d["contract_id"])) == 3_000_000


def test_db_makes_payments_and_audit_log_append_only(app):
    with app.app_context():
        d = make_approved()
        _pay(d, 1_000_000)
        db.session.commit()
        for sql in ("UPDATE payments SET amount_minor = amount_minor + 1",
                    "DELETE FROM payments",
                    "UPDATE payment_allocations SET amount_minor = amount_minor + 1",
                    "DELETE FROM payment_allocations",
                    "UPDATE audit_logs SET action = 'tampered'",
                    "DELETE FROM audit_logs"):
            with pytest.raises(DBAPIError):
                db.session.execute(text(sql))
            db.session.rollback()


def test_db_rejects_paid_amount_without_matching_allocations(app):
    with app.app_context():
        d = make_approved()
        db.session.execute(
            text("UPDATE installments SET paid_minor = paid_minor + 1 WHERE contract_id = :c AND seq = 2"),
            {"c": d["contract_id"]})
        with pytest.raises(DBAPIError):          # الفحص المؤجَّل يفشل عند الـ commit
            db.session.commit()
        db.session.rollback()