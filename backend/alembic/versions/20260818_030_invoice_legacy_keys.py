"""Add Vision-migration keys (legacy_source/legacy_id) to invoices + invoice lines.

Migration ``028_legacy_keys`` gave the legacy key to the ten other Vision-origin
tables but deliberately skipped ``invoices`` / ``invoice_line_items`` (Velocity
creates those through the freeze flow, not the CSV importer). Reconstructing the
legacy invoice history from Vision's per-shipment ledgers needs those two tables
keyed too, so a re-import UPDATEs an already-migrated invoice/line instead of
inserting a duplicate. Same shape as ``028``: a nullable ``legacy_source`` +
``legacy_id`` pair guarded by a PARTIAL unique index WHERE ``legacy_id IS NOT NULL``
(so user-created invoices, both keys NULL, stay unconstrained). Additive + nullable,
so the downgrade is a clean drop and the live app is unaffected until the importer
populates the keys.

Note: revision id kept short (alembic_version.version_num is VARCHAR(32)). The chain
position here (down_revision) is provisional -- the migration stack is linearized at
package-assembly; this only needs to be internally consistent with a working downgrade.

Revision ID: 030_invoice_legacy_keys
Revises: 029_category_composite
Create Date: 2026-08-18
"""
from alembic import op
import sqlalchemy as sa


revision = '030_invoice_legacy_keys'
down_revision = '029_category_composite'
branch_labels = None
depends_on = None

# The two invoice tables 028 left un-keyed. Order is irrelevant -- the columns are
# independent and reference nothing.
_TABLES = ["invoices", "invoice_line_items"]


def upgrade():
    for table in _TABLES:
        # Additive nullable key columns: which Vision ledger + which reconstructed row.
        op.add_column(table, sa.Column("legacy_source", sa.String(), nullable=True))
        op.add_column(table, sa.Column("legacy_id", sa.Integer(), nullable=True))
        # Partial unique guard: one Velocity row per Vision row, migrated rows only
        # (NULL-key user rows are excluded by the WHERE, so they stay unconstrained).
        op.create_index(
            f"uq_{table}_legacy",
            table,
            ["legacy_source", "legacy_id"],
            unique=True,
            postgresql_where=sa.text("legacy_id IS NOT NULL"),
        )


def downgrade():
    for table in _TABLES:
        # Drop the guard first, then the two columns (reverse of upgrade per table).
        op.drop_index(f"uq_{table}_legacy", table_name=table)
        op.drop_column(table, "legacy_id")
        op.drop_column(table, "legacy_source")
