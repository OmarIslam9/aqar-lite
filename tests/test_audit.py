from sqlalchemy import select

from app.extensions import db
from app.models import AuditLog, Company
from tests.conftest import login


def _add_events(app):
    with app.app_context():
        for part, marker in (("النخيل", "MARK-NAKHEEL"), ("الواحة", "MARK-WAHA")):
            cid = db.session.execute(select(Company.id).where(Company.name.like(f"%{part}%"))).scalar_one()
            db.session.add(AuditLog(company_id=cid, action="payment_recorded", entity="payment",
                                    entity_id=1, details={"marker": marker}))
        db.session.add(AuditLog(company_id=1, action="login_failed",
                                details={"email": "<script>alert(1)</script>"}))
        db.session.commit()


def test_manager_sees_only_own_company_events(app):
    _add_events(app)
    c = app.test_client()
    login(c, "manager1@nakheel.test")
    body = c.get("/audit/").get_data(as_text=True)
    assert "MARK-NAKHEEL" in body and "MARK-WAHA" not in body

    other = app.test_client()
    login(other, "manager1@waha.test")
    other_body = other.get("/audit/").get_data(as_text=True)
    assert "MARK-WAHA" in other_body and "MARK-NAKHEEL" not in other_body


def test_only_manager_can_read_audit_log(app):
    for email in ("sales1@nakheel.test", "accountant1@nakheel.test"):
        c = app.test_client()
        login(c, email)
        assert c.get("/audit/").status_code == 403
    assert app.test_client().get("/audit/").status_code == 302      # بدون تسجيل دخول


def test_audit_log_is_read_only_over_http(app):
    c = app.test_client()
    login(c, "manager1@nakheel.test")
    assert c.post("/audit/", data={}).status_code in (403, 405)
    assert c.delete("/audit/").status_code in (403, 405)


def test_filter_and_validation(app):
    _add_events(app)
    c = app.test_client()
    login(c, "manager1@nakheel.test")
    assert "MARK-NAKHEEL" in c.get("/audit/?action=payment_recorded").get_data(as_text=True)
    assert "MARK-NAKHEEL" not in c.get("/audit/?action=logout").get_data(as_text=True)
    assert c.get("/audit/?action=hack").status_code == 400
    assert c.get("/audit/?per_page=9999").status_code == 400
    assert c.get("/audit/?page=-1").status_code == 400


def test_user_content_is_escaped(app):
    _add_events(app)
    c = app.test_client()
    login(c, "manager1@nakheel.test")
    body = c.get("/audit/").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body