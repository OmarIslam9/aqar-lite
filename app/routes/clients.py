from math import ceil

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from pydantic import ValidationError

from ..extensions import db
from ..schemas import ClientIn, PageQuery, field_errors
from ..security import login_required, roles_required
from ..services import clients as clients_service
from ..services import reservations as reservations_service
from .helpers import clean_args

bp = Blueprint("clients", __name__, url_prefix="/clients")
FIELDS = ("name", "phone", "notes")


def _who() -> dict:
    return dict(company_id=g.user.company_id, role=g.user.role, user_id=g.user.id)


def _form(client, values, errors, status=200):
    return render_template("clients/form.html", client=client, values=values, errors=errors), status


@bp.get("/")
@login_required
def index():
    try:
        q = PageQuery(**clean_args())
    except ValidationError as exc:
        return render_template("clients/list.html", rows=[], total=0, page=1, pages=1,
                               errors=field_errors(exc)), 400
    rows, total = clients_service.list_clients(**_who(), page=q.page, per_page=q.per_page)
    return render_template("clients/list.html", rows=rows, total=total, page=q.page,
                           pages=max(1, ceil(total / q.per_page)), errors={})


@bp.get("/new")
@roles_required("sales")
def new_form():
    return _form(None, {}, {})


@bp.post("/new")
@roles_required("sales")
def create():
    values = {f: request.form.get(f, "") for f in FIELDS}
    try:
        data = ClientIn(**values)
    except ValidationError as exc:
        return _form(None, values, field_errors(exc), 400)
    client = clients_service.create_client(company_id=g.user.company_id,
                                           owner_id=g.user.id, data=data)
    db.session.commit()
    flash("تمت إضافة العميل", "ok")
    return redirect(url_for("clients.detail", client_id=client.id))


@bp.get("/<int:client_id>")
@login_required
def detail(client_id: int):
    try:
        client, owner_name = clients_service.get_client(**_who(), client_id=client_id)
    except clients_service.ClientNotFound:
        abort(404)
    reservations, _ = reservations_service.list_reservations(
        **_who(), page=1, per_page=50, client_id=client_id)
    return render_template("clients/detail.html", client=client, owner_name=owner_name,
                           reservations=reservations)


@bp.get("/<int:client_id>/edit")
@roles_required("sales")
def edit_form(client_id: int):
    try:
        client, _ = clients_service.get_client(**_who(), client_id=client_id)
    except clients_service.ClientNotFound:
        abort(404)
    return _form(client, {"name": client.name, "phone": client.phone, "notes": client.notes}, {})


@bp.post("/<int:client_id>/edit")
@roles_required("sales")
def update(client_id: int):
    try:
        client, _ = clients_service.get_client(**_who(), client_id=client_id)
    except clients_service.ClientNotFound:
        abort(404)
    values = {f: request.form.get(f, "") for f in FIELDS}
    try:
        data = ClientIn(**values)
    except ValidationError as exc:
        return _form(client, values, field_errors(exc), 400)
    clients_service.update_client(company_id=g.user.company_id, owner_id=g.user.id,
                                  client_id=client_id, data=data)
    db.session.commit()
    flash("تم حفظ بيانات العميل", "ok")
    return redirect(url_for("clients.detail", client_id=client_id))