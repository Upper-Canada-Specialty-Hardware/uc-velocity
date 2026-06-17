"""Invoice versioning: current_version column + invoice snapshot tables

Mirrors the quote snapshot/revert machinery for invoices so a "created on" date edit
(and future invoice edits) can bump a version, land in an audit trail with the acting
user, and be reverted. entity_created_at on invoice_snapshots holds the invoice's
created_at at snapshot time so a revert can restore the date as well as the line items.

Note: revision id kept short - alembic_version.version_num is VARCHAR(32).

Revision ID: 024_invoice_versioning
Revises: 023_bulk_markup_50
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa


revision = '024_invoice_versioning'
down_revision = '023_bulk_markup_50'
branch_labels = None
depends_on = None


def _table_exists(conn, table_name: str) -> bool:
    return conn.dialect.has_table(conn, table_name)


def upgrade():
    conn = op.get_bind()

    # invoices.current_version (invoice's own snapshot version)
    conn.execute(sa.text(
        "ALTER TABLE invoices ADD COLUMN IF NOT EXISTS current_version INTEGER NOT NULL DEFAULT 0"
    ))

    # invoice_snapshots
    if not _table_exists(conn, 'invoice_snapshots'):
        op.create_table(
            'invoice_snapshots',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('invoice_id', sa.Integer(), sa.ForeignKey('invoices.id'), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('action_type', sa.String(), nullable=False),
            sa.Column('action_description', sa.String()),
            sa.Column('created_at', sa.DateTime()),
            sa.Column('entity_created_at', sa.DateTime()),
            sa.Column('actor_user_id', sa.String()),
            sa.Column('actor_email', sa.String()),
        )
        op.create_index('ix_invoice_snapshots_id', 'invoice_snapshots', ['id'])

    # invoice_line_item_snapshots
    if not _table_exists(conn, 'invoice_line_item_snapshots'):
        op.create_table(
            'invoice_line_item_snapshots',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('snapshot_id', sa.Integer(), sa.ForeignKey('invoice_snapshots.id'), nullable=False),
            sa.Column('original_line_item_id', sa.Integer()),
            sa.Column('quote_line_item_id', sa.Integer()),
            sa.Column('item_type', sa.String(), nullable=False),
            sa.Column('description', sa.String()),
            sa.Column('unit_price', sa.Float()),
            sa.Column('qty_ordered', sa.Integer()),
            sa.Column('qty_fulfilled_this_invoice', sa.Integer()),
            sa.Column('qty_fulfilled_total', sa.Integer()),
            sa.Column('qty_pending_after', sa.Integer()),
            sa.Column('labor_id', sa.Integer()),
            sa.Column('part_id', sa.Integer()),
            sa.Column('misc_id', sa.Integer()),
        )
        op.create_index('ix_invoice_line_item_snapshots_id', 'invoice_line_item_snapshots', ['id'])


def downgrade():
    conn = op.get_bind()

    if _table_exists(conn, 'invoice_line_item_snapshots'):
        op.drop_table('invoice_line_item_snapshots')
    if _table_exists(conn, 'invoice_snapshots'):
        op.drop_table('invoice_snapshots')

    conn.execute(sa.text("ALTER TABLE invoices DROP COLUMN IF EXISTS current_version"))
