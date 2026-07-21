"""Add 'staff' to profiletype enum and make profiles.pst nullable

Issue #163: UCA staff profiles. Staff become a third ProfileType alongside
customer/vendor. Staff have no Provincial Tax Number, so profiles.pst is
relaxed to nullable.

Note: revision id kept short - alembic_version.version_num is VARCHAR(32).

Revision ID: 025_staff_profile_type
Revises: 024_invoice_versioning
Create Date: 2026-07-21
"""
from alembic import op
import sqlalchemy as sa


revision = '025_staff_profile_type'
down_revision = '024_invoice_versioning'
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
    # Restore the NOT NULL constraint. Any staff rows with a null pst must be
    # cleaned up before downgrading; on an empty schema this is a no-op.
    op.alter_column('profiles', 'pst', existing_type=sa.String(), nullable=False)

    # Note: Postgres cannot drop a single value from an enum without recreating
    # the type, so removing 'staff' is intentionally not attempted here. A full
    # downgrade to base drops the profiletype enum entirely via the baseline.
