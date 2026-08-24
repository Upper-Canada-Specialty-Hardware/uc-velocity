"""Add Vision-migration keys (legacy_source/legacy_id) + a partial-unique guard.

Every table whose rows can be imported from the legacy UC Vision database gets a
nullable ``legacy_source`` (which Vision table the row came from) and ``legacy_id``
(that row's Vision primary key), plus a PARTIAL unique index on the pair WHERE
``legacy_id IS NOT NULL``. This lets the Vision importer re-run safely: it matches
on the key to UPDATE an already-imported row instead of inserting a duplicate,
while rows a user created directly in Velocity (both keys NULL) stay unconstrained.
Everything is additive + nullable, so the downgrade is a clean drop and the live
app is unaffected until the importer populates the keys.

NOTE (rebase before merge): originally this forked directly off 024, colliding with
the staff-profile branch that also forks from 024 (two Alembic heads). It is now
chained after ``025_staff_profile_type`` to linearize the migration stack to a single
head so it can build. At LANDING onto current master this must be repointed again: it
still needs to chain after master's then-current head (the feature migrations that
merged after this branch forked), so the whole stack stays single-headed.

Revision ID: 028_legacy_keys
Revises: 025_staff_profile_type
Create Date: 2026-08-06
"""
from alembic import op
import sqlalchemy as sa


revision = '028_legacy_keys'
down_revision = '025_staff_profile_type'
branch_labels = None
depends_on = None

# The 10 Vision-origin tables that carry the legacy key. Order is irrelevant --
# the columns are independent and reference nothing.
_TABLES = [
    "categories", "profiles", "parts", "labor", "miscellaneous", "projects",
    "quotes", "quote_line_items", "purchase_orders", "po_line_items",
]


def upgrade():
    for table in _TABLES:
        # Additive nullable key columns: which Vision table + which Vision row.
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
