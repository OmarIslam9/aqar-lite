from sqlalchemy import func, select

from ..extensions import db
from ..models import AuditLog, User


def record(*, company_id: int | None, actor_id: int | None, action: str,
           entity: str | None = None, entity_id: int | None = None,
           details: dict | None = None, ip: str | None = None) -> None:
    db.session.add(AuditLog(company_id=company_id, actor_id=actor_id, action=action,
                            entity=entity, entity_id=entity_id,
                            details=details or {}, ip=ip))


def list_entries(*, company_id: int, action: str | None, page: int, per_page: int):
    """سجل شركة واحدة فقط (الشركة من الجلسة). يرجّع [(AuditLog, اسم المنفّذ أو None)]."""
    conds = [AuditLog.company_id == company_id]
    if action:
        conds.append(AuditLog.action == action)
    total = db.session.execute(select(func.count(AuditLog.id)).where(*conds)).scalar_one()
    rows = db.session.execute(
        select(AuditLog, User.full_name)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(*conds).order_by(AuditLog.id.desc())
        .limit(per_page).offset((page - 1) * per_page)
    ).all()
    return rows, total