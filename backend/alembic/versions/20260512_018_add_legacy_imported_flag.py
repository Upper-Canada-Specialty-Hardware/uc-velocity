"""Add legacy_imported flag to quotes and purchase_orders.

UX-7 asks for a way to distinguish records that came in from the original
UC Vision CSV migration from records that are real, fulfilled work. The flag
is purely additive — defaults to FALSE for everything — so existing data is
untouched. A separate, explicit backfill (or a manual update) can mark
pre-migration rows later if desired; this migration deliberately does not
attempt that classification itself.

Revision ID: 018_legacy_imported_flag
Revises: 017_add_fk_indexes
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa


revision = '018_legacy_imported_flag'
down_revision = '017_add_fk_indexes'
branch_labels = None
depends_on = None


# (table, column) pairs — kept as a tuple so upgrade/downgrade stay symmetric.
_COLUMNS = [
    ('quotes', 'legacy_imported'),
    ('purchase_orders', 'legacy_imported'),
]


def _column_exists(table: str, column: str) -> bool:
    """Idempotent guard matching the rest of the migration chain."""
    conn = op.get_bind()
    result = conn.execute(sa.text(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = :t AND column_name = :c
        LIMIT 1
        """
    ), {"t": table, "c": column}).scalar()
    return bool(result)


def upgrade() -> None:
    for table, column in _COLUMNS:
        if not _column_exists(table, column):
            op.add_column(
                table,
                sa.Column(column, sa.Boolean(), nullable=False, server_default=sa.text('FALSE')),
            )


def downgrade() -> None:
    for table, column in _COLUMNS:
        if _column_exists(table, column):
            op.drop_column(table, column)
