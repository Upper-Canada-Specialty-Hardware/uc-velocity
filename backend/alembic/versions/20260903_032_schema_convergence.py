"""Converge the production schema and a fresh migration-built schema

Production was created before the initial baseline migration existed (the tables
were built straight from the models), while every other database (CI, previews,
local) is built by running the migration chain from scratch. The two diverged in
small ways that ``alembic check`` reports as drift and that the models never
declared:

* production lacks nine column defaults the baseline migration sets;
* production has four primary-key indexes the chain never creates, and both lack
  two more the models ask for;
* production allows NULL in ``miscellaneous.unit_price`` (the model says NOT NULL);
* production stores ``quotes.work_description`` as TEXT (the chain uses VARCHAR);
* production has a default on ``miscellaneous.is_system_item`` the chain lacks.

Every step here is additive and idempotent, so the same migration is correct on
production and on an empty database. The one constraint tightening
(``unit_price`` NOT NULL) refuses to run if any NULL exists rather than backfill
a price. After this migration the models describe both schemas exactly and CI's
``alembic check`` gate can be made blocking.

Revision ID: 032_schema_converge
Revises: 031_staff_roles
Create Date: 2026-09-03
"""
from alembic import op
import sqlalchemy as sa


revision = '032_schema_converge'
down_revision = '031_staff_roles'
branch_labels = None
depends_on = None


# (table, column, default, existing_type, existing_nullable) -- the defaults the
# baseline migration sets on a fresh database but production never received.
# All are the same values the application already applies in Python.
_COLUMN_DEFAULTS = [
    ('invoices', 'status', 'Sent', sa.String(), True),
    ('labor', 'hours', '1', sa.Float(), False),
    ('po_line_items', 'quantity', '1', sa.Integer(), True),
    ('projects', 'status', 'active', sa.String(), True),
    ('quote_line_item_snapshots', 'is_deleted', sa.text('false'), sa.Boolean(), True),
    ('quote_line_items', 'quantity', '1', sa.Integer(), True),
    ('quote_line_items', 'qty_pending', '0', sa.Integer(), True),
    ('quote_line_items', 'qty_fulfilled', '0', sa.Integer(), True),
    ('quotes', 'current_version', '0', sa.Integer(), True),
    # Present on production, absent on a fresh database.
    ('miscellaneous', 'is_system_item', sa.text('false'), sa.Boolean(), True),
]

# Primary-key indexes the models declare (``index=True`` on ``id``). Production
# has the four ``po_*`` ones; no database has the other two.
_ID_INDEXES = [
    ('cost_codes', 'ix_cost_codes_id'),
    ('po_line_item_snapshots', 'ix_po_line_item_snapshots_id'),
    ('po_receiving_line_items', 'ix_po_receiving_line_items_id'),
    ('po_receivings', 'ix_po_receivings_id'),
    ('po_snapshots', 'ix_po_snapshots_id'),
    ('system_rates', 'ix_system_rates_id'),
]


def upgrade():
    """Bring production and a fresh database to one shape.

    Raises:
        RuntimeError: if ``miscellaneous.unit_price`` holds a NULL, so the NOT
            NULL constraint cannot be applied without inventing a price.
    """
    conn = op.get_bind()                                       # live connection for the guard query

    # 1. Column defaults -- SET DEFAULT is metadata-only in Postgres (no rewrite)
    #    and a no-op where the default already exists.
    for table, column, default, existing_type, existing_nullable in _COLUMN_DEFAULTS:
        op.alter_column(
            table, column,
            server_default=default,                            # the value the app already uses
            existing_type=existing_type,
            existing_nullable=existing_nullable,
        )

    # 2. Primary-key indexes -- IF NOT EXISTS keeps this idempotent on production,
    #    which already has four of them.
    for table, index_name in _ID_INDEXES:
        op.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON {table} (id)')

    # 3. quotes.work_description -- production is TEXT; VARCHAR -> TEXT is a
    #    metadata-only widening on Postgres and a no-op where it is already TEXT.
    op.alter_column(
        'quotes', 'work_description',
        type_=sa.Text(),
        existing_type=sa.String(),
        existing_nullable=True,
    )

    # 4. miscellaneous.unit_price NOT NULL -- guarded: refuse rather than backfill.
    null_prices = conn.execute(
        sa.text('SELECT count(*) FROM miscellaneous WHERE unit_price IS NULL')
    ).scalar()
    if null_prices:                                            # any NULL -> stop, a human decides the price
        raise RuntimeError(
            f'{null_prices} miscellaneous row(s) have a NULL unit_price; '
            'fix them before applying 032_schema_converge.'
        )
    op.alter_column(
        'miscellaneous', 'unit_price',
        nullable=False,                                        # model already says NOT NULL; fresh DBs already enforce it
        existing_type=sa.Float(),
    )


def downgrade():
    """Reverse each step. Drops the six indexes even where production had four
    of them before this migration; they are redundant with the primary key, so
    nothing depends on them."""
    op.alter_column(
        'miscellaneous', 'unit_price',
        nullable=True,
        existing_type=sa.Float(),
    )
    op.alter_column(
        'quotes', 'work_description',
        type_=sa.String(),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    for table, index_name in _ID_INDEXES:
        op.execute(f'DROP INDEX IF EXISTS {index_name}')
    for table, column, default, existing_type, existing_nullable in _COLUMN_DEFAULTS:
        op.alter_column(
            table, column,
            server_default=None,                               # DROP DEFAULT
            existing_type=existing_type,
            existing_nullable=existing_nullable,
        )
