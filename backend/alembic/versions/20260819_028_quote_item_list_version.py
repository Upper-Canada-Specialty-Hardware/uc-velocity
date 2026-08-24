"""Add quotes.item_list_version (issue #202)

Separates two things the single ``current_version`` counter used to conflate:

- ``current_version`` stays the internal audit/snapshot counter — it still bumps
  on every snapshot (edit, invoice, date edit, revert), so revert and the
  editor's staleness detection are unchanged.
- ``item_list_version`` is the NEW visible version shown in the quote number and
  invoice number. It advances only when the Parts/Labour/Misc line items change.

Existing rows are backfilled ``item_list_version = current_version`` so that no
already-issued quote or invoice number changes. Additive and non-destructive.

Mirrors the proven ``invoices.current_version`` pattern (Integer, server_default
'0') from migration 024, so ``alembic check`` sees no drift.

Note: revision id kept short - alembic_version.version_num is VARCHAR(32).

Revision ID: 028_quote_item_list_ver
Revises: 027_usd_pricing
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = '028_quote_item_list_ver'
down_revision = '027_usd_pricing'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # Add the visible item-list version, defaulting new rows to 0.
    conn.execute(sa.text(
        "ALTER TABLE quotes ADD COLUMN IF NOT EXISTS item_list_version INTEGER NOT NULL DEFAULT 0"
    ))
    # Backfill existing quotes so their visible number is unchanged by this migration.
    conn.execute(sa.text(
        "UPDATE quotes SET item_list_version = current_version"
    ))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE quotes DROP COLUMN IF EXISTS item_list_version"
    ))
