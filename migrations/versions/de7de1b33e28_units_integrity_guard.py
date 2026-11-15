"""units integrity guard

Revision ID: de7de1b33e28
Revises: cb699ff47d72
Create Date: 2026-11-15 13:49:47.688254

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'de7de1b33e28'
down_revision = 'cb699ff47d72'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE FUNCTION units_guard() RETURNS trigger AS $$
    BEGIN
      IF NEW.company_id <> OLD.company_id THEN
        RAISE EXCEPTION 'unit company is immutable';
      END IF;
      IF NEW.status <> OLD.status AND NOT (
           (OLD.status = 'available' AND NEW.status = 'reserved')
        OR (OLD.status = 'reserved' AND NEW.status IN ('available', 'sold'))
      ) THEN
        RAISE EXCEPTION 'illegal unit status transition';
      END IF;
      IF NEW.price_minor <> OLD.price_minor AND OLD.status <> 'available' THEN
        RAISE EXCEPTION 'cannot change price of a reserved or sold unit';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    """)
    op.execute("""
    CREATE TRIGGER trg_units_guard BEFORE UPDATE ON units
    FOR EACH ROW EXECUTE FUNCTION units_guard();
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_units_guard ON units")
    op.execute("DROP FUNCTION IF EXISTS units_guard()")
