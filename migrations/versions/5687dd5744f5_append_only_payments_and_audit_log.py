"""append-only payments and audit log

Revision ID: 5687dd5744f5
Revises: 7765ad5e5935
Create Date: 2026-11-15 19:14:04.326011

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5687dd5744f5'
down_revision = '7765ad5e5935'
branch_labels = None
depends_on = None


def upgrade():
    # 1) سجلات لا تُعدَّل ولا تُحذف: الدفعات وتوزيعها وسجل التدقيق
    op.execute("""
    CREATE FUNCTION forbid_change() RETURNS trigger AS $$
    BEGIN
      RAISE EXCEPTION 'append-only table: updates and deletes are not allowed';
    END;
    $$ LANGUAGE plpgsql;
    """)
    for table in ("payments", "payment_allocations", "audit_logs"):
        op.execute(f"""
        CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION forbid_change();
        """)

    # 2) وقت الـ commit: توزيع الدفعة لازم يساوي مبلغها بالظبط
    op.execute("""
    CREATE FUNCTION payments_check_allocations() RETURNS trigger AS $$
    DECLARE allocated bigint;
    BEGIN
      SELECT COALESCE(SUM(amount_minor), 0) INTO allocated
      FROM payment_allocations WHERE payment_id = NEW.id;
      IF allocated <> NEW.amount_minor THEN
        RAISE EXCEPTION 'payment is not fully allocated to installments';
      END IF;
      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_payments_fully_allocated
    AFTER INSERT ON payments DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION payments_check_allocations();
    """)

    # 3) وقت الـ commit: المدفوع في القسط لازم يساوي مجموع توزيعاته
    op.execute("""
    CREATE FUNCTION installments_check_paid() RETURNS trigger AS $$
    DECLARE allocated bigint;
    BEGIN
      SELECT COALESCE(SUM(amount_minor), 0) INTO allocated
      FROM payment_allocations WHERE installment_id = NEW.id;
      IF allocated <> NEW.paid_minor THEN
        RAISE EXCEPTION 'installment paid amount does not match its allocations';
      END IF;
      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_installments_paid_matches
    AFTER UPDATE ON installments DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION installments_check_paid();
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_installments_paid_matches ON installments")
    op.execute("DROP FUNCTION IF EXISTS installments_check_paid()")
    op.execute("DROP TRIGGER IF EXISTS trg_payments_fully_allocated ON payments")
    op.execute("DROP FUNCTION IF EXISTS payments_check_allocations()")
    for table in ("audit_logs", "payment_allocations", "payments"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS forbid_change()")
