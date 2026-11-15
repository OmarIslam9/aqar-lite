from ..extensions import db
from ..models import AuditLog


def record(*, company_id: int | None, actor_id: int | None, action: str,
           entity: str | None = None, entity_id: int | None = None,
           details: dict | None = None, ip: str | None = None) -> None:
    db.session.add(AuditLog(company_id=company_id, actor_id=actor_id, action=action,
                            entity=entity, entity_id=entity_id,
                            details=details or {}, ip=ip))