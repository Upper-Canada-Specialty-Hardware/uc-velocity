"""Add USD pricing fields (issue #203)

Adds two additive, non-null columns with defaults so existing rows backfill
cleanly and no data is touched:

- ``parts.is_usd_priced`` (BOOLEAN, default ``false``) — flags a part whose
  ``cost`` is entered in USD. Every existing part becomes ``false``.
- ``company_settings.usd_to_cad_rate`` (DOUBLE PRECISION, default ``1.38``) —
  the live USD→CAD exchange rate used to convert USD-priced parts to CAD when
  they are added to a quote. The single settings row is backfilled to ``1.38``.

Mirrors the proven ``current_version INTEGER NOT NULL DEFAULT 0`` pattern from
migration 024 (boolean/numeric server-default columns that pass ``alembic check``).

Note: revision id kept short - alembic_version.version_num is VARCHAR(32).

Revision ID: 027_usd_pricing
Revises: 026_labor_product_code
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = '027_usd_pricing'
down_revision = '026_labor_product_code'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # Flag for USD-priced parts; backfills every existing row to false.
    conn.execute(sa.text(
        "ALTER TABLE parts ADD COLUMN IF NOT EXISTS is_usd_priced BOOLEAN NOT NULL DEFAULT false"
    ))
    # Live exchange rate; backfills the singleton settings row to 1.38.
    conn.execute(sa.text(
        "ALTER TABLE company_settings ADD COLUMN IF NOT EXISTS usd_to_cad_rate DOUBLE PRECISION NOT NULL DEFAULT 1.38"
    ))


def downgrade():
    conn = op.get_bind()
    # Reverse order of upgrade; both drops are idempotent.
    conn.execute(sa.text(
        "ALTER TABLE company_settings DROP COLUMN IF EXISTS usd_to_cad_rate"
    ))
    conn.execute(sa.text(
        "ALTER TABLE parts DROP COLUMN IF EXISTS is_usd_priced"
    ))
