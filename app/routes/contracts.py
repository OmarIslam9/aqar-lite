import uuid
from math import ceil

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from pydantic import ValidationError

from ..extensions import db
from ..money import format_money, minor_to_input, to_minor
from ..schemas import (ContractIn, PageQuery, PaymentIn, ReservationRef,
                       field_errors)
from ..security import login_required, roles_required
from ..services import contracts as svc
from ..services import payments as payments_service
from ..services import reservations as reservations_service
from ..services import units as units_service
from ..services.schedule import min_final_price
from ..timeutil import today_local
from .helpers import clean_args

bp = Blueprint("contracts", __name__, url_prefix="/contracts")
FORM_FIELDS = ("final_price", "down_payment", "installments_count", "start_date")
INACTIVE_MSG = "انتهى هذا الحجز أو لم يعد نشطاً، ولا يمكن إنشاء عقد منه."
EXISTS_MSG = "يوجد عقد لهذا الحجز بالفعل."
NOT_APPROVED_MSG = "لا يمكن تسجيل دفعات على عقد غير معتمد."
KEY_CONFLICT_MSG = "رمز الطلب هذا استُخدم لدفعة مختلفة. أعد تحميل الصفحة وحاول مرة أخرى."


def _who() -> dict:
    return dict(company_id=g.user.company_id, role=g.user.role, user_id=g.user.id)


def _context(reservation_id: int) -> dict:
    try:
        reservation, unit, client, existing_id = svc.get_reservation_for_contract(
            company_id=g.user.company_id, sales_id=g.user.id, reservation_id=reservation_id)
    except svc.ReservationNotFound:
        abort(404)
    return {"reservation": reservation, "unit": unit, "client": client,
            "existing_contract_id": existing_id, "min_final": min_final_price(unit.price_minor)}


def _form(ctx: dict, values: dict, errors: dict, status: int = 200):
    return render_template("contracts/form.html", values=values, errors=errors, **ctx), status


def _render_detail(contract_id: int, *, values: dict | None = None,
                   errors: dict | None = None, status: int = 200):
    if reservations_service.expire_due(g.user.company_id):
        db.session.commit()
    try:
        contract, unit, client, sales_name, reservation = svc.get_contract(
            **_who(), contract_id=contract_id)
    except svc.ContractNotFound:
        abort(404)
    rows = svc.schedule_rows(contract, today_local())
    payments = payments_service.history(company_id=g.user.company_id, contract_id=contract.id)
    return render_template(
        "contracts/detail.html", contract=contract, unit=unit, client=client,
        sales_name=sales_name, reservation=reservation, rows=rows, totals=svc.totals(rows),
        payments=payments, pay_values=values or {}, pay_errors=errors or {},
        pay_key=uuid.uuid4().hex), status       # مفتاح جديد لكل عرض للنموذج


@bp.get("/")
@login_required
def index():
    try:
        q = PageQuery(**clean_args())
    except ValidationError as exc:
        return render_template("contracts/list.html", rows=[], total=0, page=1, pages=1,
                               errors=field_errors(exc)), 400
    rows, total = svc.list_contracts(**_who(), page=q.page, per_page=q.per_page)
    return render_template("contracts/list.html", rows=rows, total=total, page=q.page,
                           pages=max(1, ceil(total / q.per_page)), errors={})


@bp.get("/new")
@roles_required("sales")
def new_form():
    try:
        ref = ReservationRef(**clean_args())
    except ValidationError:
        abort(400)
    if reservations_service.expire_due(g.user.company_id):
        db.session.commit()
    ctx = _context(ref.reservation_id)
    values = {"final_price": minor_to_input(ctx["unit"].price_minor), "down_payment": "0.00",
              "installments_count": "12", "start_date": today_local().isoformat()}
    return _form(ctx, values, {})


@bp.post("/new")
@roles_required("sales")
def create():
    try:
        ref = ReservationRef(reservation_id=request.form.get("reservation_id", ""))
    except ValidationError:
        abort(400)
    values = {f: request.form.get(f, "") for f in FORM_FIELDS}
    try:
        data = ContractIn(reservation_id=ref.reservation_id, **values)
    except ValidationError as exc:
        return _form(_context(ref.reservation_id), values, field_errors(exc), 400)

    try:
        contract = svc.create_contract(company_id=g.user.company_id, sales_id=g.user.id, data=data)
    except svc.ReservationNotFound:
        db.session.rollback()
        abort(404)
    except svc.InvalidTerms as exc:
        db.session.rollback()
        return _form(_context(ref.reservation_id), values, exc.errors, 400)
    except svc.ReservationNotActive:
        db.session.rollback()
        reservations_service.expire_due(g.user.company_id)
        db.session.commit()
        return _form(_context(ref.reservation_id), values, {"form": INACTIVE_MSG}, 409)
    except svc.ContractExists:
        db.session.rollback()
        return _form(_context(ref.reservation_id), values, {"form": EXISTS_MSG}, 409)

    contract_id = contract.id
    db.session.commit()
    flash("تم إنشاء العقد كمسودة، بانتظار اعتماد المحاسب", "ok")
    return redirect(url_for("contracts.detail", contract_id=contract_id))


@bp.get("/<int:contract_id>")
@login_required
def detail(contract_id: int):
    return _render_detail(contract_id)


@bp.post("/<int:contract_id>/approve")
@roles_required("accountant")
def approve(contract_id: int):
    back = redirect(url_for("contracts.detail", contract_id=contract_id))
    try:
        svc.approve_contract(company_id=g.user.company_id, accountant_id=g.user.id,
                             contract_id=contract_id)
    except svc.ContractNotFound:
        db.session.rollback()
        abort(404)
    except svc.AlreadyApproved:
        db.session.rollback()
        flash("هذا العقد معتمد بالفعل", "error")
        return back
    except svc.ReservationNotActive:
        db.session.rollback()
        reservations_service.expire_due(g.user.company_id)
        db.session.commit()
        flash("انتهى حجز هذه الوحدة، ولا يمكن اعتماد العقد", "error")
        return back
    except units_service.InvalidTransition:
        db.session.rollback()
        flash("حالة الوحدة لا تسمح باعتماد العقد", "error")
        return back
    db.session.commit()
    flash("تم اعتماد العقد وتوليد جدول الأقساط", "ok")
    return back


@bp.post("/<int:contract_id>/payments")
@roles_required("accountant")
def pay(contract_id: int):
    # الـ header يتقدّم على حقل النموذج (للعملاء البرمجيين). الشركة والمستخدم من الجلسة فقط.
    raw = {"amount": request.form.get("amount", ""),
           "idempotency_key": request.headers.get("Idempotency-Key")
           or request.form.get("idempotency_key", "")}
    try:
        data = PaymentIn(**raw)
    except ValidationError as exc:
        return _render_detail(contract_id, values={"amount": raw["amount"]},
                              errors=field_errors(exc), status=400)

    try:
        result = payments_service.record_payment(
            company_id=g.user.company_id, accountant_id=g.user.id, contract_id=contract_id,
            amount_minor=to_minor(data.amount), idempotency_key=data.idempotency_key,
            ip=request.remote_addr)
    except payments_service.ContractNotFound:
        db.session.rollback()
        abort(404)
    except payments_service.ContractNotApproved:
        db.session.rollback()
        return _render_detail(contract_id, values={"amount": raw["amount"]},
                              errors={"form": NOT_APPROVED_MSG}, status=409)
    except payments_service.Overpayment as exc:
        db.session.rollback()
        msg = f"المبلغ يتجاوز المتبقي من العقد ({format_money(exc.remaining_minor)})"
        return _render_detail(contract_id, values={"amount": raw["amount"]},
                              errors={"amount": msg}, status=400)
    except payments_service.KeyConflict:
        db.session.rollback()
        return _render_detail(contract_id, values={"amount": raw["amount"]},
                              errors={"form": KEY_CONFLICT_MSG}, status=409)

    db.session.commit()
    if result.duplicate:
        flash("تم تسجيل هذه الدفعة مسبقاً، ولم تُسجَّل مرة أخرى", "ok")
    else:
        flash("تم تسجيل الدفعة وتوزيعها على الأقساط", "ok")
    return redirect(url_for("contracts.detail", contract_id=contract_id))