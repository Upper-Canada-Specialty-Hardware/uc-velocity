"""Add actor columns to quote_snapshots and po_snapshots (audit trail)

Records who performed each action:
  - actor_user_id: the verified Clerk user id (JWT ``sub``)
  - actor_email:   resolved email, for display

Both are nullable — pre-existing snapshots and any unauthenticated writes simply
have no actor. Every quote/PO/invoice/receiving/revert action funnels through
create_snapshot / create_po_snapshot, so these two tables cover the whole trail.

Note: revision id kept short — alembic_version.version_num is VARCHAR(32).

Revision ID: 022_snapshot_actor
Revises: 021_invoice_numbering
Create Date: 2026-05-28
"""
from alembic import op
import sqlalchemy as sa


revision = '022_snapshot_actor'
down_revision = '021_invoice_numbering'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text("ALTER TABLE quote_snapshots ADD COLUMN IF NOT EXISTS actor_user_id VARCHAR"))
    conn.execute(sa.text("ALTER TABLE quote_snapshots ADD COLUMN IF NOT EXISTS actor_email VARCHAR"))
    conn.execute(sa.text("ALTER TABLE po_snapshots ADD COLUMN IF NOT EXISTS actor_user_id VARCHAR"))
    conn.execute(sa.text("ALTER TABLE po_snapshots ADD COLUMN IF NOT EXISTS actor_email VARCHAR"))


def downgrade():
    op.drop_column('po_snapshots', 'actor_email')
    op.drop_column('po_snapshots', 'actor_user_id')
    op.drop_column('quote_snapshots', 'actor_email')
    op.drop_column('quote_snapshots', 'actor_user_id')
