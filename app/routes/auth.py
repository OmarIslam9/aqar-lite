from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import (Blueprint, current_app, g, redirect, render_template,
                   request, session, url_for)
from pydantic import ValidationError
from sqlalchemy import select

from ..extensions import db
from ..models import User
from ..schemas import LoginIn, field_errors
from ..security import (SESSION_COOKIE, SESSION_TTL, audit, create_session,
                        csrf_token, destroy_session, login_required,
                        login_throttled, purge_expired_sessions)

bp = Blueprint("auth", __name__)
ph = PasswordHasher()
DUMMY_HASH = ph.hash("dummy-password-for-constant-time")  # يوحّد زمن الرد لو البريد غير موجود
GENERIC_FAIL = "البريد الإلكتروني أو كلمة المرور غير صحيحة"


@bp.get("/login")
def login_form():
    if g.user:
        return redirect(url_for("main.home"))
    return render_template("login.html", errors={}, email="")


@bp.post("/login")
def login():
    raw_email = request.form.get("email", "")[:255]
    try:
        data = LoginIn(email=request.form.get("email", ""),
                       password=request.form.get("password", ""))
    except ValidationError as exc:
        return render_template("login.html", errors=field_errors(exc), email=raw_email), 400

    email = data.email.lower()
    ip = request.remote_addr

    if login_throttled(email, ip):
        audit("login_throttled", details={"email": email})
        db.session.commit()
        return render_template("login.html", email=email, errors={
            "form": "محاولات كثيرة. حاول مرة أخرى بعد قليل."}), 429

    user = db.session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    try:
        ph.verify(user.password_hash if user else DUMMY_HASH, data.password)
        ok = user is not None and user.is_active
    except (VerificationError, InvalidHashError):
        ok = False

    if not ok:
        audit("login_failed", details={"email": email},
              company_id=user.company_id if user else None)
        db.session.commit()
        return render_template("login.html", email=email, errors={"form": GENERIC_FAIL}), 401

    if ph.check_needs_rehash(user.password_hash):
        user.password_hash = ph.hash(data.password)
    purge_expired_sessions()
    token = create_session(user)
    audit("login_success", company_id=user.company_id, actor_id=user.id)
    db.session.commit()

    session.clear()          # تدوير الـ CSRF بعد الدخول
    csrf_token()
    resp = redirect(url_for("main.home"))
    resp.set_cookie(SESSION_COOKIE, token, max_age=int(SESSION_TTL.total_seconds()),
                    httponly=True, secure=current_app.config["COOKIE_SECURE"], samesite="Lax")
    return resp


@bp.post("/logout")
@login_required
def logout():
    destroy_session(g.session_token)          # يُبطل الجلسة في الخادم فعلياً
    audit("logout")
    db.session.commit()
    session.clear()
    resp = redirect(url_for("auth.login_form"))
    resp.delete_cookie(SESSION_COOKIE)
    return resp