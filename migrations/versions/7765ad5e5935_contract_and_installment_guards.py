"""contract and installment guards

Revision ID: 7765ad5e5935
Revises: de7de1b33e28
Create Date: 2026-11-15 18:51:19.051544

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7765ad5e5935'
down_revision = 'de7de1b33e28'
branch_labels = None
depends_on = None


def upgrade():
    # 1) العقد: لا حذف، والتعديل الوحيد المسموح = الانتقال من draft إلى approved بدون تغيير الشروط
    op.execute("""
    CREATE FUNCTION contracts_guard() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'contracts cannot be deleted';
      END IF;
      IF OLD.status = 'approved' THEN
        RAISE EXCEPTION 'approved contracts are immutable';
      END IF;
      IF NEW.status <> 'approved'
         OR NEW.id <> OLD.id OR NEW.company_id <> OLD.company_id
         OR NEW.unit_id <> OLD.unit_id OR NEW.client_id <> OLD.client_id
         OR NEW.reservation_id <> OLD.reservation_id OR NEW.sales_id <> OLD.sales_id
         OR NEW.list_price_minor <> OLD.list_price_minor
         OR NEW.final_price_minor <> OLD.final_price_minor
         OR NEW.down_payment_minor <> OLD.down_payment_minor
         OR NEW.installments_count <> OLD.installments_count
         OR NEW.start_date <> OLD.start_date THEN
        RAISE EXCEPTION 'contract terms are frozen; only approval is allowed';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER trg_contracts_guard BEFORE UPDATE OR DELETE ON contracts
    FOR EACH ROW EXECUTE FUNCTION contracts_guard();
    """)

    # 2) الأقساط: لا حذف، والمسموح فقط زيادة paid_minor
    op.execute("""
    CREATE FUNCTION installments_guard() RETURNS trigger AS $$
    BEGIN
      IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'installments cannot be deleted';
      END IF;
      IF NEW.id <> OLD.id OR NEW.company_id <> OLD.company_id
         OR NEW.contract_id <> OLD.contract_id OR NEW.seq <> OLD.seq
         OR NEW.due_date <> OLD.due_date OR NEW.amount_minor <> OLD.amount_minor THEN
        RAISE EXCEPTION 'installment schedule is frozen; only paid_minor may change';
      END IF;
      IF NEW.paid_minor < OLD.paid_minor THEN
        RAISE EXCEPTION 'paid amount cannot decrease';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER trg_installments_guard BEFORE UPDATE OR DELETE ON installments
    FOR EACH ROW EXECUTE FUNCTION installments_guard();
    """)

    # 3) وقت الـ commit: مجموع الأقساط لازم يساوي السعر النهائي للعقد المعتمد
    op.execute("""
    CREATE FUNCTION contracts_check_schedule() RETURNS trigger AS $$
    DECLARE total bigint;
    BEGIN
      IF NEW.status = 'approved' THEN
        SELECT COALESCE(SUM(amount_minor), 0) INTO total
        FROM installments WHERE contract_id = NEW.id;
        IF total <> NEW.final_price_minor THEN
          RAISE EXCEPTION 'installments total does not match contract final price';
        END IF;
      END IF;
      RETURN NULL;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE CONSTRAINT TRIGGER trg_contracts_schedule_total
    AFTER UPDATE ON contracts DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION contracts_check_schedule();
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_contracts_schedule_total ON contracts")
    op.execute("DROP FUNCTION IF EXISTS contracts_check_schedule()")
    op.execute("DROP TRIGGER IF EXISTS trg_installments_guard ON installments")
    op.execute("DROP FUNCTION IF EXISTS installments_guard()")
    op.execute("DROP TRIGGER IF EXISTS trg_contracts_guard ON contracts")
    op.execute("DROP FUNCTION IF EXISTS contracts_guard()")
