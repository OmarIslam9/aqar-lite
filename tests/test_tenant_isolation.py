from sqlalchemy import select

from app.extensions import db
from app.models import Client, Company, Unit, User
from tests.conftest import csrf, login


def _waha_unit_id(app) -> int:
    with app.app_context():
        cid = db.session.execute(select(Company.id).where(Company.name.like("%الواحة%"))).scalar_one()
        return db.session.execute(select(Unit.id).where(Unit.company_id == cid)).scalars().first()


def test_manager_cannot_touch_other_company_unit(app):
    unit_id = _waha_unit_id(app)
    c = app.test_client()
    login(c, "manager1@nakheel.test")
    assert c.get(f"/units/{unit_id}/edit").status_code == 404
    data = {"csrf_token": csrf(c), "project": "x", "number": "1", "type": "t",
            "area_sqm": "10", "rooms": "1", "price": "100"}
    assert c.post(f"/units/{unit_id}/edit", data=data).status_code == 404


def test_list_never_contains_other_company_units(app):
    c = app.test_client()
    login(c, "manager1@nakheel.test")
    body = c.get("/units/?per_page=50").get_data(as_text=True)
    assert "A-101" in body and body.count("<tr>") == 13  # رأس الجدول + 12 وحدة
    login_other = app.test_client()
    login(login_other, "manager1@waha.test")
    assert login_other.get("/units/?per_page=50").get_data(as_text=True).count("<tr>") == 13


def test_sales_sees_only_own_clients(app):
    with app.app_context():
        other = db.session.execute(
            select(Client).join(User, User.id == Client.owner_id)
            .where(User.email == "sales2@nakheel.test")).scalars().first()
        other_id, other_name = other.id, other.name
    c = app.test_client()
    login(c, "sales1@nakheel.test")
    assert other_name not in c.get("/clients/").get_data(as_text=True)
    assert c.get(f"/clients/{other_id}").status_code == 404
    assert c.get(f"/clients/{other_id}/edit").status_code == 404


def test_sales_cannot_reserve_other_company_unit(app):
    unit_id = _waha_unit_id(app)
    c = app.test_client()
    login(c, "sales1@nakheel.test")
    resp = c.post("/reservations/new", data={"csrf_token": csrf(c), "unit_id": unit_id,
                                             "client_id": 1})
    assert resp.status_code == 404


def test_role_enforced_on_server(app):
    c = app.test_client()
    login(c, "accountant1@nakheel.test")
    assert c.get("/units/new").status_code == 403
    assert c.get("/clients/new").status_code == 403
    assert c.get("/reservations/new?unit_id=1").status_code == 403