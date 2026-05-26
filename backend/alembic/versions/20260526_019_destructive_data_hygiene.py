"""Destructive data hygiene: unescape CSV quotes, backfill legacy_imported, drop dead columns.

Closes UX-7 (#99). Counterpart to the additive 018_legacy_imported_flag — runs
the three operations the previous PR deliberately deferred because they cannot
be undone by Alembic alone.

Three operations, all in a single transaction so the batch is atomic:

1. Replace any run of four consecutive double-quote characters in
   parts.description / labor.description / miscellaneous.description with a
   single double-quote. The over-escaped sequence is a CSV artifact from the
   original UC Vision import (see inventory_health report's
   `description_has_escaped_quotes` detector for the matching read-side).

2. Set legacy_imported = TRUE on quotes and purchase_orders whose created_at
   predates the user-adoption cutoff (LEGACY_CUTOFF below). The cutoff is
   the day Clerk auth shipped — before that, the only writes to these tables
   were the bulk CSV import plus dev testing on the unauthenticated app, so
   flagging them as "imported" is a conservative match for the badge's UX
   purpose. Change LEGACY_CUTOFF before running if a different boundary is
   appropriate.

3. Drop three columns that the UI no longer surfaces and the data model no
   longer carries:
     - profiles.website
     - cost_codes.gp_cost_code_properties
     - cost_codes.uch_dept_properties

Revision ID: 019_destructive_data_hygiene
Revises: 018_legacy_imported_flag
Create Date: 2026-05-26
"""
from alembic import op
import sqlalchemy as sa


revision = '019_destructive_data_hygiene'
down_revision = '018_legacy_imported_flag'
branch_labels = None
depends_on = None


# Cutoff for the legacy_imported backfill. Anything created strictly before
# this date is flagged. See module docstring for rationale.
LEGACY_CUTOFF = '2026-04-09'

# Inventory tables whose `description` column came from the CSV import and
# is exposed in the UI. Line-item override descriptions are user-typed and
# would not carry the four-quote artifact, so they're deliberately excluded.
_DESCRIPTION_TABLES = ('parts', 'labor', 'miscellaneous')

# (table, column) pairs to drop. Kept as a tuple so upgrade/downgrade
# share a single source of truth.
_DROPPED_COLUMNS = (
    ('profiles', 'website'),
    ('cost_codes', 'gp_cost_code_properties'),
    ('cost_codes', 'uch_dept_properties'),
)


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
    conn = op.get_bind()

    # 1. Unescape "" -> " on rows that contain the four-quote artifact.
    # The filter is purely a no-op optimization; REPLACE on a row without
    # the pattern is a no-op anyway.
    for table in _DESCRIPTION_TABLES:
        conn.execute(sa.text(
            f"UPDATE {table} "
            f"SET description = REPLACE(description, '\"\"\"\"', '\"') "
            f"WHERE description LIKE '%\"\"\"\"%'"
        ))

    # 2. Backfill legacy_imported on rows created before the cutoff.
    # Guard on legacy_imported = FALSE so re-running the migration after
    # someone manually un-flags a row does not silently re-flag it.
    for table in ('quotes', 'purchase_orders'):
        conn.execute(
            sa.text(
                f"UPDATE {table} "
                f"SET legacy_imported = TRUE "
                f"WHERE legacy_imported = FALSE AND created_at < :cutoff"
            ),
            {"cutoff": LEGACY_CUTOFF},
        )

    # 3. Drop dead columns the UI no longer surfaces.
    for table, column in _DROPPED_COLUMNS:
        if _column_exists(table, column):
            op.drop_column(table, column)


def downgrade() -> None:
    """Restore dropped columns; string rewrites and the backfill cannot be undone.

    The historical character sequence in `description` fields and the original
    FALSE state of `legacy_imported` are not recoverable from this revision
    alone — a restore-from-backup is required to roll those parts back.
    """
    for table, column in _DROPPED_COLUMNS:
        if not _column_exists(table, column):
            op.add_column(table, sa.Column(column, sa.String(), nullable=True))
