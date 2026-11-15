import json
import os

from flask import Flask, render_template, request
from werkzeug.exceptions import HTTPException

from .extensions import db, migrate
from .labels import AUDIT_ACTION_LABELS
from .money import format_money
from .security import check_csrf, csrf_token, load_current_user
from .timeutil import local_dt

ROLE_LABELS = {"manager": "مدير الشركة", "sales": "مندوب مبيعات", "accountant": "محاسب"}
STATUS_LABELS = {"available": "متاحة", "reserved": "محجوزة", "sold": "مباعة"}
CONTRACT_LABELS = {"draft": "مسودة", "approved": "معتمد"}
INSTALLMENT_LABELS = {"pending": "قيد الانتظار", "partial": "مدفوع جزئياً",
                      "paid": "مدفوع", "overdue": "متأخر"}
HTTP_MESSAGES = {
    400: "طلب غير صالح", 401: "يلزم تسجيل الدخول", 403: "غير مسموح لك بهذا الإجراء",
    404: "الصفحة غير موجودة", 405: "طريقة الطلب غير مسموحة", 409: "تعارض في البيانات",
    413: "حجم الطلب كبير", 429: "محاولات كثيرة، حاول لاحقاً",
}


def create_app() -> Flask:
    app = Flask(__name__)
    cookie_secure = os.environ.get("COOKIE_SECURE", "1") == "1"
    secret_key = os.environ["SECRET_KEY"]
    if cookie_secure and (secret_key.startswith("dev-") or len(secret_key) < 32):
        # وضع الإنتاج (HTTPS) يرفض التشغيل بمفتاح افتراضي أو ضعيف
        raise RuntimeError("SECRET_KEY is weak or a development default; set a strong random value")

    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ["DATABASE_URL"],
        SECRET_KEY=secret_key,
        MAX_CONTENT_LENGTH=1024 * 1024,
        COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if os.environ.get("TRUST_PROXY") == "1":
        # فعّله فقط خلف reverse proxy موثوق، وإلا يمكن تزوير X-Forwarded-For
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    db.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401
    from .routes import audit, auth, clients, contracts, main, reservations, units
    from .seed import seed_command

    app.cli.add_command(seed_command)
    for module in (auth, main, units, clients, reservations, contracts, audit):
        app.register_blueprint(module.bp)

    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["local_dt"] = local_dt
    app.jinja_env.filters["compact_json"] = (
        lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True) if d else "")

    @app.before_request
    def guard():
        if request.endpoint == "static":
            return None
        check_csrf()
        load_current_user()
        return None

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "ROLE_LABELS": ROLE_LABELS,
                "STATUS_LABELS": STATUS_LABELS, "CONTRACT_LABELS": CONTRACT_LABELS,
                "INSTALLMENT_LABELS": INSTALLMENT_LABELS,
                "AUDIT_ACTION_LABELS": AUDIT_ACTION_LABELS}

    @app.after_request
    def security_headers(resp):
        h = resp.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "same-origin"
        h["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        h["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie_secure:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.endpoint != "static":
            h["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        code = exc.code or 500
        if code >= 500:
            db.session.rollback()
            app.logger.error("server error: %s", exc, exc_info=exc)
        return render_template("error.html", code=code,
                               message=HTTP_MESSAGES.get(code, "حدث خطأ غير متوقع")), code

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        db.session.rollback()
        app.logger.exception("unhandled error")
        return render_template("error.html", code=500, message="حدث خطأ غير متوقع"), 500

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app