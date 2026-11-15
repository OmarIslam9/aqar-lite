import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from .services import audit as audit_service
from flask import abort, g, redirect, request, session, url_for
from sqlalchemy import delete, func, select

from .extensions import db
from .models import AuditLog, AuthSession, User

SESSION_COOKIE = "sid"
SESSION_TTL = timedelta(hours=8)
THROTTLE_WINDOW = timedelta(minutes=15)
MAX_FAILS_PER_EMAIL = 5
MAX_FAILS_PER_IP = 30


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- الجلسات (على الخادم) ----------
def create_session(user: User) -> str:
    """يرجّع التوكن الخام للكوكي. المخزَّن في DB هو الـ hash فقط."""
    token = secrets.token_urlsafe(32)
    db.session.add(AuthSession(id=_hash(token), user_id=user.id,
                               expires_at=utcnow() + SESSION_TTL))
    return token


def destroy_session(token: str) -> None:
    db.session.execute(delete(AuthSession).where(AuthSession.id == _hash(token)))


def purge_expired_sessions() -> None:
    db.session.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))


def load_current_user() -> None:
    g.user = None
    g.session_token = None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return
    user = db.session.execute(
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(AuthSession.id == _hash(token),
               AuthSession.expires_at > utcnow(),
               User.is_active.is_(True))
    ).scalar_one_or_none()
    if user:
        g.user = user
        g.session_token = token


# ---------- CSRF ----------
def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf() -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    expected = session.get("csrf", "")
    if not expected or not hmac.compare_digest(sent, expected):
        abort(403)


# ---------- الصلاحيات ----------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if getattr(g, "user", None) is None:
            if request.path.startswith("/api/"):
                abort(401)
            return redirect(url_for("auth.login_form"))
        return view(*args, **kwargs)
    return wrapped


def roles_required(*roles: str):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if g.user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---------- عزل الشركات: الشركة تُؤخذ من الجلسة فقط ----------
def scoped(model):
    return select(model).where(model.company_id == g.user.company_id)


def get_scoped_or_404(model, obj_id: int):
    """سجل شركة أخرى = 404 (لا نكشف وجوده)."""
    obj = db.session.execute(scoped(model).where(model.id == obj_id)).scalar_one_or_none()
    if obj is None:
        abort(404)
    return obj


# ---------- سجل التدقيق ----------
def audit(action: str, entity: str | None = None, entity_id: int | None = None,
          details: dict | None = None, company_id: int | None = None,
          actor_id: int | None = None) -> None:
    user = getattr(g, "user", None)
    audit_service.record(
        company_id=company_id if company_id is not None else (user.company_id if user else None),
        actor_id=actor_id if actor_id is not None else (user.id if user else None),
        action=action, entity=entity, entity_id=entity_id,
        details=details, ip=request.remote_addr)


# ---------- حماية من تخمين كلمات المرور ----------
def login_throttled(email: str, ip: str | None) -> bool:
    since = utcnow() - THROTTLE_WINDOW
    base = select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "login_failed", AuditLog.created_at >= since)
    by_email = db.session.execute(
        base.where(AuditLog.details["email"].astext == email)).scalar_one()
    by_ip = db.session.execute(base.where(AuditLog.ip == ip)).scalar_one()
    return by_email >= MAX_FAILS_PER_EMAIL or by_ip >= MAX_FAILS_PER_IP