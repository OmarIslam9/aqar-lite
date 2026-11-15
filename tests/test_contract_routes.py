from app.extensions import db
from app.models import Contract
from app.timeutil import today_local
from tests.conftest import csrf, login
from tests.factories import make_draft, make_reservation, minor_to_decimal


def _status(app, contract_id: int) -> str:
    with app.app_context():
        return db.session.get(Contract, contract_id).status


def test_only_accountant_can_approve(app):
    with app.app_context():
        d = make_draft()
    cid = d["contract_id"]
    for email in ("sales1@nakheel.test", "manager1@nakheel.test"):
        c = app.test_client()
        login(c, email)
        assert c.post(f"/contracts/{cid}/approve", data={"csrf_token": csrf(c)}).status_code == 403
    assert _status(app, cid) == "draft"

    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    assert c.post(f"/contracts/{cid}/approve", data={"csrf_token": csrf(c)}).status_code == 302
    assert _status(app, cid) == "approved"
    body = c.get(f"/contracts/{cid}").get_data(as_text=True)
    assert "معتمد" in body and "المبلغ المتبقي" in body


def test_accountant_cannot_create_contract(app):
    with app.app_context():
        ctx = make_reservation()
    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    assert c.get(f"/contracts/new?reservation_id={ctx['reservation_id']}").status_code == 403


def test_contract_visibility_and_tenant_isolation(app):
    with app.app_context():
        d = make_draft()                         # عقد sales1 في شركة النخيل
    cid = d["contract_id"]

    other_sales = app.test_client()
    login(other_sales, "sales2@nakheel.test")
    assert other_sales.get(f"/contracts/{cid}").status_code == 404
    assert f"/contracts/{cid}" not in other_sales.get("/contracts/").get_data(as_text=True)

    other_company = app.test_client()
    login(other_company, "accountant1@waha.test")
    assert other_company.get(f"/contracts/{cid}").status_code == 404
    assert other_company.post(f"/contracts/{cid}/approve",
                              data={"csrf_token": csrf(other_company)}).status_code == 404

    for email, sees_approve in (("accountant1@nakheel.test", True), ("manager1@nakheel.test", False),
                                ("sales1@nakheel.test", False)):
        c = app.test_client()
        login(c, email)
        resp = c.get(f"/contracts/{cid}")
        assert resp.status_code == 200
        assert (f"/contracts/{cid}/approve" in resp.get_data(as_text=True)) is sees_approve


def test_create_contract_form_validates_discount(app):
    with app.app_context():
        ctx = make_reservation()
    c = app.test_client()
    login(c, "sales1@nakheel.test")
    form = {"csrf_token": csrf(c), "reservation_id": ctx["reservation_id"],
            "final_price": str(minor_to_decimal(ctx["list_minor"] * 90 // 100)),
            "down_payment": "0.00", "installments_count": "12",
            "start_date": today_local().isoformat()}
    resp = c.post("/contracts/new", data=form)
    assert resp.status_code == 400 and "الخصم يتجاوز" in resp.get_data(as_text=True)

    form["final_price"] = str(minor_to_decimal(ctx["list_minor"]))
    assert c.post("/contracts/new", data=form).status_code == 302
    assert c.post("/contracts/new", data=form).status_code == 409     # نفس الحجز مرتين