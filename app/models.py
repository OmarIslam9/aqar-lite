from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime,
                        ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .extensions import db

TS = dict(type_=DateTime(timezone=True), server_default=func.now())


class Company(db.Model):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(**TS)


class User(db.Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('manager','sales','accountant')", name="role_valid"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(**TS)


class AuthSession(db.Model):
    """جلسة على الخادم: الـ id هو SHA-256 للتوكن (التوكن نفسه لا يُخزَّن)."""
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(**TS)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Unit(db.Model):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("company_id", "project", "number", name="project_number"),
        CheckConstraint("status IN ('available','reserved','sold')", name="status_valid"),
        CheckConstraint("price_minor > 0", name="price_positive"),
        CheckConstraint("area_sqm > 0", name="area_positive"),
        CheckConstraint("rooms >= 0", name="rooms_nonneg"),
        Index("ix_units_list", "company_id", "status", "price_minor"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    project: Mapped[str] = mapped_column(String(200))
    number: Mapped[str] = mapped_column(String(50))
    type: Mapped[str] = mapped_column(String(50))
    area_sqm: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    rooms: Mapped[int] = mapped_column(Integer)
    price_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="available", server_default="available")
    created_at: Mapped[datetime] = mapped_column(**TS)


class Client(db.Model):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # المندوب
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(30))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(**TS)


class Reservation(db.Model):
    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("status IN ('active','expired','converted')", name="status_valid"),
        # حجز نشط واحد فقط للوحدة: خط الدفاع الأخير ضد السباق
        Index("uq_reservations_one_active_per_unit", "unit_id", unique=True,
              postgresql_where=text("status = 'active'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    sales_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(**TS)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Contract(db.Model):
    __tablename__ = "contracts"
    __table_args__ = (
        CheckConstraint("status IN ('draft','approved')", name="status_valid"),
        CheckConstraint("final_price_minor <= list_price_minor", name="no_markup"),
        CheckConstraint("final_price_minor * 100 >= list_price_minor * 95", name="max_discount_5pct"),
        CheckConstraint("down_payment_minor >= 0 AND down_payment_minor <= final_price_minor",
                        name="down_payment_range"),
        CheckConstraint("installments_count BETWEEN 1 AND 360", name="count_range"),
        CheckConstraint("status = 'draft' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)",
                        name="approved_has_approver"),
        Index("uq_contracts_one_approved_per_unit", "unit_id", unique=True,
              postgresql_where=text("status = 'approved'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("units.id"))
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.id"), unique=True)
    sales_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    list_price_minor: Mapped[int] = mapped_column(BigInteger)
    final_price_minor: Mapped[int] = mapped_column(BigInteger)
    down_payment_minor: Mapped[int] = mapped_column(BigInteger)
    installments_count: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(**TS)


class Installment(db.Model):
    """seq=0 هي الدفعة الأولى (المقدّم) وتستحق في start_date، ثم 1..n شهرياً."""
    __tablename__ = "installments"
    __table_args__ = (
        UniqueConstraint("contract_id", "seq", name="contract_seq"),
        CheckConstraint("seq >= 0", name="seq_nonneg"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("paid_minor >= 0 AND paid_minor <= amount_minor", name="paid_range"),
        Index("ix_installments_contract_due", "contract_id", "due_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"))
    seq: Mapped[int] = mapped_column(Integer)
    due_date: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    paid_minor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    # الحالة (pending/partial/paid/overdue) تُحسب ولا تُخزَّن


class Payment(db.Model):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        UniqueConstraint("company_id", "idempotency_key", name="idempotency"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    received_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(**TS)


class PaymentAllocation(db.Model):
    __tablename__ = "payment_allocations"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="amount_positive"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), index=True)
    installment_id: Mapped[int] = mapped_column(ForeignKey("installments.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_company_created", "company_id", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    entity: Mapped[str | None] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = mapped_column(**TS)