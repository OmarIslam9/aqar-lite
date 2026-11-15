from math import ceil

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from pydantic import ValidationError
from sqlalchemy import select

from ..extensions import db
from ..models import Client, Unit
from ..schemas import PageQuery, ReserveIn, ReserveQuery, field_errors
from ..security import get_scoped_or_404, login_required, roles_required
from ..services import reservations as svc
from ..services import units as units_service
from ..timeutil import local_dt
from .helpers import clean_args

bp = Blueprint("reservations", __name__, url_prefix="/reservations")
TAKEN_MSG = "هذه الوحدة لم تعد متاحة. تم حجزها للتو بواسطة مندوب آخر."


def _my_clients():
    return db.session.execute(
        select(Client).where(Client.company_id == g.user.company_id,
                             Client.owner_id == g.user.id).order_by(Client.name)
    ).scalars().all()


def _form(unit, values, errors, status=200):
    return render_template("reservations/form.html", unit=unit, clients=_my_clients(),
                           values=values, errors=errors), status


@bp.get("/")
@login_required
def index():
    if svc.expire_due(g.user.company_id):      # الانتهاء بدون تدخل يدوي
        db.session.commit()
    try:
        q = PageQuery(**clean_args())
    except ValidationError as exc:
        return render_template("reservations/list.html", rows=[], total=0, page=1, pages=1,
                               errors=field_errors(exc)), 400
    rows, total = svc.list_reservations(company_id=g.user.company_id, role=g.user.role,
                                        user_id=g.user.id, page=q.page, per_page=q.per_page)
    return render_template("reservations/list.html", rows=rows, total=total, page=q.page,
                           pages=max(1, ceil(total / q.per_page)), errors={})


@bp.get("/new")
@roles_required("sales")
def new_form():
    try:
        q = ReserveQuery(**clean_args())
    except ValidationError:
        abort(400)
    if svc.expire_due(g.user.company_id):
        db.session.commit()
    unit = get_scoped_or_404(Unit, q.unit_id)
    return _form(unit, {"unit_id": str(unit.id)}, {})


@bp.post("/new")
@roles_required("sales")
def create():
    values = {"unit_id": request.form.get("unit_id", ""),
              "client_id": request.form.get("client_id", "")}
    try:
        data = ReserveIn(**values)
    except ValidationError as exc:
        errors = field_errors(exc)
        if "unit_id" in errors or "form" in errors:
            abort(400)
        unit = get_scoped_or_404(Unit, int(values["unit_id"]))
        return _form(unit, values, errors, 400)

    try:
        reservation = svc.reserve_unit(company_id=g.user.company_id, sales_id=g.user.id,
                                       unit_id=data.unit_id, client_id=data.client_id)
    except (svc.ClientNotFound, units_service.UnitNotFound):
        db.session.rollback()
        abort(404)
    except svc.UnitNotAvailable:
        db.session.rollback()
        unit = get_scoped_or_404(Unit, data.unit_id)
        return _form(unit, values, {"form": TAKEN_MSG}, 409)

    db.session.commit()
    flash(f"تم حجز الوحدة حتى {local_dt(reservation.expires_at)}", "ok")
    return redirect(url_for("reservations.index"))