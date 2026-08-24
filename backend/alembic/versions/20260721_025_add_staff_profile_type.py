"""Add 'staff' to profiletype enum and make profiles.pst nullable

Issue #163: UCA staff profiles. Staff become a third ProfileType alongside
customer/vendor. Staff have no Provincial Tax Number, so profiles.pst is
relaxed to nullable.

Note: revision id kept short - alembic_version.version_num is VARCHAR(32).

Note (rebase onto master): originally forked off 024_invoice_versioning. Re-parented
onto master's current head (028_quote_item_list_ver) so this branch chains linearly
after the feature migrations -- a single Alembic head, no fork.

Revision ID: 025_staff_profile_type
Revises: 028_quote_item_list_ver
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa


revision = '025_staff_profile_type'
down_revision = '028_quote_item_list_ver'
branch_labels = None
depends_on = None


def upgrade():
    # Add the new enum value. Postgres 12+ permits ALTER TYPE ... ADD VALUE
    # inside a transaction as long as the value is not *used* in the same
    # transaction (it is only added here; application inserts use it later).
    op.execute("ALTER TYPE profiletype ADD VALUE IF NOT EXISTS 'staff'")

    # Staff have no Provincial Tax Number, so pst is no longer required.
    op.alter_column('profiles', 'pst', existing_type=sa.String(), nullable=True)


def downgrade():
    # Backfill NULL pst (staff rows) to '' BEFORE restoring NOT NULL, so a
    # downgrade on a LIVE DB that already has staff profiles doesn't abort with
    # "column pst contains null values". pst has no format/CHECK constraint, so
    # '' satisfies the restored constraint. (The empty-schema CI job never has
    # staff rows, so this path was previously untested.)
    op.execute("UPDATE profiles SET pst = '' WHERE pst IS NULL")
    op.alter_column('profiles', 'pst', existing_type=sa.String(), nullable=False)

    # Note: Postgres cannot drop a single value from an enum without recreating
    # the type, so removing 'staff' is intentionally not attempted here. A full
    # downgrade to base drops the profiletype enum entirely via the baseline.
