"""Add per-quote description override to quote line items (issue #178)

Adds a nullable ``description_override`` column to ``quote_line_items`` and
``quote_line_item_snapshots``. This is a per-quote display override, distinct
from the existing ``description`` field (which for migrated lines holds the
original UC Vision line description). Additive and nullable: existing rows are
untouched, so migrated quotes render exactly as before until a user edits a
line's description.

Note: revision id kept short - alembic_version.version_num is VARCHAR(32).

Revision ID: 025_line_desc_override
Revises: 024_invoice_versioning
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision = '025_line_desc_override'
down_revision = '024_invoice_versioning'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE quote_line_items "
        "ADD COLUMN IF NOT EXISTS description_override VARCHAR"
    ))
    conn.execute(sa.text(
        "ALTER TABLE quote_line_item_snapshots "
        "ADD COLUMN IF NOT EXISTS description_override VARCHAR"
    ))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE quote_line_item_snapshots "
        "DROP COLUMN IF EXISTS description_override"
    ))
    conn.execute(sa.text(
        "ALTER TABLE quote_line_items "
        "DROP COLUMN IF EXISTS description_override"
    ))
