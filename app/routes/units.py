from math import ceil

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from pydantic import ValidationError
from ..services import reservations as reservations_service
from ..extensions import db
from ..models import Unit
from ..money import minor_to_input
from ..schemas import UnitIn, UnitQuery, field_errors
from ..security import get_scoped_or_404, login_required, roles_required
from ..services import units as units_service

bp = Blueprint("units", __name__, url_prefix="/units")
FORM_FIELDS = ("project", "number", "type", "area_sqm", "rooms", "price")
PRICE_LOCKED_MSG = "لا يمكن تعديل سعر وحدة محجوزة أو مباعة"
DUPLICATE_MSG = "رقم الوحدة مستخدم بالفعل في هذا المشروع"


def _submitted() -> dict[str, str]:
    return {name: request.form.get(name, "") for name in FORM_FIELDS}


def _form_page(unit, values, errors, status=200):
    return render_template("units/form.html", unit=unit, values=values, errors=errors), status


@bp.get("/")
@login_required
def index():
    if reservations_service.expire_due(g.user.company_id):
        db.session.commit()
    args = {k: v for k, v in request.args.items() if v != ""}
    try:
        q = UnitQuery(**args)
    except ValidationError as exc:
        return render_template("units/list.html", units=[], total=0, pages=0, q=None,
                               args=args, base_args={}, errors=field_errors(exc)), 400

    units, total = units_service.list_units(g.user.company_id, q)  # الشركة من الجلسة فقط
    pages = max(1, ceil(total / q.per_page))
    base_args = {k: v for k, v in args.items() if k != "page"}
    return render_template("units/list.html", units=units, total=total, pages=pages, q=q,
                           args=args, base_args=base_args, errors={})


@bp.get("/new")
@roles_required("manager")
def new_form():
    return _form_page(None, {}, {})


@bp.post("/new")
@roles_required("manager")
def create():
    values = _submitted()
    try:
        data = UnitIn(**values)
    except ValidationError as exc:
        return _form_page(None, values, field_errors(exc), 400)
    try:
        units_service.create_unit(company_id=g.user.company_id, actor_id=g.user.id, data=data)
    except units_service.DuplicateUnit:
        return _form_page(None, values, {"number": DUPLICATE_MSG}, 409)
    db.session.commit()
    flash("تمت إضافة الوحدة", "ok")
    return redirect(url_for("units.index"))


@bp.get("/<int:unit_id>/edit")
@roles_required("manager")
def edit_form(unit_id: int):
    unit = get_scoped_or_404(Unit, unit_id)
    values = {"project": unit.project, "number": unit.number, "type": unit.type,
              "area_sqm": str(unit.area_sqm), "rooms": str(unit.rooms),
              "price": minor_to_input(unit.price_minor)}
    return _form_page(unit, values, {})


@bp.post("/<int:unit_id>/edit")
@roles_required("manager")
def update(unit_id: int):
    unit = get_scoped_or_404(Unit, unit_id)
    values = _submitted()
    try:
        data = UnitIn(**values)
    except ValidationError as exc:
        return _form_page(unit, values, field_errors(exc), 400)
    try:
        units_service.update_unit(company_id=g.user.company_id, actor_id=g.user.id,
                                  unit_id=unit_id, data=data)
    except units_service.UnitNotFound:
        abort(404)
    except units_service.PriceLocked:
        return _form_page(unit, values, {"price": PRICE_LOCKED_MSG}, 409)
    except units_service.DuplicateUnit:
        return _form_page(unit, values, {"number": DUPLICATE_MSG}, 409)
    db.session.commit()
    flash("تم حفظ التعديلات", "ok")
    return redirect(url_for("units.index"))