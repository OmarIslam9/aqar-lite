import os

from flask import Flask, render_template
from werkzeug.exceptions import HTTPException

from .extensions import db, migrate
from .security import check_csrf, csrf_token, load_current_user

ROLE_LABELS = {"manager": "مدير الشركة", "sales": "مندوب مبيعات", "accountant": "محاسب"}
HTTP_MESSAGES = {
    400: "طلب غير صالح", 401: "يلزم تسجيل الدخول", 403: "غير مسموح لك بهذا الإجراء",
    404: "الصفحة غير موجودة", 405: "طريقة الطلب غير مسموحة", 413: "حجم الطلب كبير",
    429: "محاولات كثيرة، حاول لاحقاً",
}


def create_app() -> Flask:
    app = Flask(__name__)
    cookie_secure = os.environ.get("COOKIE_SECURE", "1") == "1"
    app.config.update(
        SQLALCHEMY_DATABASE_URI=os.environ["DATABASE_URL"],
        SECRET_KEY=os.environ["SECRET_KEY"],
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=1024 * 1024,
        COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=cookie_secure,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    db.init_app(app)
    migrate.init_app(app, db)

    from . import models  # noqa: F401
    from .routes import auth, main
    from .seed import seed_command

    app.cli.add_command(seed_command)
    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)

    @app.before_request
    def guard():
        from flask import request
        if request.endpoint == "static":
            return None
        check_csrf()
        load_current_user()
        return None

    @app.context_processor
    def inject():
        return {"csrf_token": csrf_token, "ROLE_LABELS": ROLE_LABELS}

    @app.after_request
    def security_headers(resp):
        from flask import request
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
        # رسالة عامة للمستخدم، والتفاصيل في سجل الخادم فقط
        return render_template("error.html", code=code,
                               message=HTTP_MESSAGES.get(code, "حدث خطأ غير متوقع")), code

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app