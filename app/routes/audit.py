from math import ceil

from flask import Blueprint, g, render_template
from pydantic import ValidationError

from ..schemas import AuditQuery, field_errors
from ..security import roles_required
from ..services import audit as audit_service
from .helpers import clean_args

bp = Blueprint("audit", __name__, url_prefix="/audit")


@bp.get("/")
@roles_required("manager")          # المدير فقط، والقراءة فقط (لا يوجد POST)
def index():
    try:
        q = AuditQuery(**clean_args())
    except ValidationError as exc:
        return render_template("audit/list.html", rows=[], total=0, page=1, pages=1,
                               action=None, args={}, errors=field_errors(exc)), 400
    rows, total = audit_service.list_entries(company_id=g.user.company_id, action=q.action,
                                             page=q.page, per_page=q.per_page)
    return render_template("audit/list.html", rows=rows, total=total, page=q.page,
                           pages=max(1, ceil(total / q.per_page)), action=q.action,
                           args={"action": q.action} if q.action else {}, errors={})