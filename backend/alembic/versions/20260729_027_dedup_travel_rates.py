"""Dedup active system_rates and guard against re-duplication

The ``system_rates`` table accumulated duplicate ACTIVE rows per tier (the
Travel Distance dropdown was showing each tier 4x). This migration:

1. Soft-deactivates the redundant rows: for each (rate_type, description) it
   keeps the lowest-id active row and sets is_active=false on the rest. This is
   a non-destructive UPDATE (no row is deleted). Nothing has a foreign key to
   system_rates, and quote line items reference the underlying miscellaneous
   row rather than the rate, so existing quotes are unaffected.
2. Adds a PARTIAL unique index so at most one ACTIVE row can exist per
   (rate_type, description) going forward. It is partial (WHERE is_active) so
   the soft-delete workflow can still keep historical inactive rows that share
   a description.

Idempotent: safe on a DB with no duplicates (the UPDATE touches 0 rows) and the
index is created IF NOT EXISTS.

Note: revision id kept short - alembic_version.version_num is VARCHAR(32).

Revision ID: 027_dedup_travel_rates
Revises: 026_labor_product_code
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa


revision = '027_dedup_travel_rates'
down_revision = '026_labor_product_code'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # 1. Keep the lowest-id active row per (rate_type, description); soft-deactivate the rest.
    conn.execute(sa.text(
        """
        UPDATE system_rates
        SET is_active = false
        WHERE is_active = true
          AND id NOT IN (
              SELECT MIN(id)
              FROM system_rates
              WHERE is_active = true
              GROUP BY rate_type, description
          )
        """
    ))
    # 2. Enforce at most one ACTIVE row per (rate_type, description) going forward.
    conn.execute(sa.text(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_system_rates_active_rate_desc
        ON system_rates (rate_type, description)
        WHERE is_active = true
        """
    ))


def downgrade():
    conn = op.get_bind()
    # Only the index is reversible. The soft-deactivation is intentionally NOT
    # undone: there is no record of which rows were the original active
    # duplicates, and reactivating them would restore the bug.
    conn.execute(sa.text(
        "DROP INDEX IF EXISTS uq_system_rates_active_rate_desc"
    ))
