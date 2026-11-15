import uuid

from sqlalchemy import func, select

from app.extensions import db
from app.models import Payment
from app.money import format_money
from tests.conftest import csrf, login
from tests.factories import make_approved, make_draft, minor_to_decimal


def _count(app) -> int:
    with app.app_context():
        return db.session.execute(select(func.count()).select_from(Payment)).scalar_one()


def _form(c, amount="1000.00", key=None):
    return {"csrf_token": csrf(c), "amount": amount, "idempotency_key": key or uuid.uuid4().hex}


def test_only_accountant_can_record_payments(app):
    with app.app_context():
        d = make_approved()
    for email in ("sales1@nakheel.test", "sales2@nakheel.test", "manager1@nakheel.test"):
        c = app.test_client()
        login(c, email)
        assert c.post(f"/contracts/{d['contract_id']}/payments", data=_form(c)).status_code == 403
    assert _count(app) == 0


def test_payment_updates_remaining_and_history(app):
    with app.app_context():
        d = make_approved()
    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    resp = c.post(f"/contracts/{d['contract_id']}/payments", data=_form(c, "1000.00"))
    assert resp.status_code == 302
    body = c.get(f"/contracts/{d['contract_id']}").get_data(as_text=True)
    assert format_money(d["final_minor"] - 100_000) in body
    assert "سجل الدفعات" in body

    sales = app.test_client()                      # المندوب صاحب العقد يشوف الدفعات (قراءة فقط)
    login(sales, "sales1@nakheel.test")
    sales_body = sales.get(f"/contracts/{d['contract_id']}").get_data(as_text=True)
    assert "سجل الدفعات" in sales_body and "تسجيل الدفعة" not in sales_body


def test_double_submit_with_same_key_records_one_payment(app):
    with app.app_context():
        d = make_approved()
    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    form = _form(c)
    url = f"/contracts/{d['contract_id']}/payments"
    assert c.post(url, data=form).status_code == 302
    second = c.post(url, data=form, follow_redirects=True)
    assert second.status_code == 200 and "مسبقاً" in second.get_data(as_text=True)
    assert _count(app) == 1


def test_idempotency_key_header_takes_precedence(app):
    with app.app_context():
        d = make_approved()
    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    url = f"/contracts/{d['contract_id']}/payments"
    headers = {"Idempotency-Key": uuid.uuid4().hex}
    assert c.post(url, data=_form(c), headers=headers).status_code == 302
    assert c.post(url, data=_form(c), headers=headers).status_code == 302   # مفتاح النموذج مختلف
    assert _count(app) == 1


def test_overpayment_shows_error_next_to_field(app):
    with app.app_context():
        d = make_approved()
    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    too_much = str(minor_to_decimal(d["final_minor"] + 1))
    resp = c.post(f"/contracts/{d['contract_id']}/payments", data=_form(c, too_much))
    assert resp.status_code == 400 and "يتجاوز المتبقي" in resp.get_data(as_text=True)
    assert _count(app) == 0


def test_invalid_amount_or_key_is_rejected(app):
    with app.app_context():
        d = make_approved()
    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    url = f"/contracts/{d['contract_id']}/payments"
    for amount in ("0", "-5", "abc", "10.001", ""):
        assert c.post(url, data=_form(c, amount)).status_code == 400
    assert c.post(url, data=_form(c, key="short")).status_code == 400
    assert _count(app) == 0


def test_draft_contract_payment_is_rejected(app):
    with app.app_context():
        d = make_draft()
    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    resp = c.post(f"/contracts/{d['contract_id']}/payments", data=_form(c))
    assert resp.status_code == 409 and "غير معتمد" in resp.get_data(as_text=True)
    assert _count(app) == 0


def test_other_company_accountant_gets_404(app):
    with app.app_context():
        d = make_approved()
    c = app.test_client()
    login(c, "accountant1@waha.test")
    assert c.post(f"/contracts/{d['contract_id']}/payments", data=_form(c)).status_code == 404
    assert _count(app) == 0