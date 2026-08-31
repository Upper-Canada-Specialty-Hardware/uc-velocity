"""Make profiles.address and profiles.postal_code nullable

Staff profiles imported from Vision (tblEmployees) usually have no address or
postal code -- Vision stores '' for most employees. The Profile model already
declares both columns nullable (added with the staff-profile work), but no
migration ever relaxed the database constraint: 025 only relaxed `pst`, and 031
only added `staff_roles`. The Vision->Velocity import therefore failed on the
first staff row with a NOT NULL violation during the cutover rehearsal.

This brings the schema in line with the model. It is a pure relaxation: no data
changes, existing rows keep their values, the app keeps writing addresses for
customers and vendors exactly as before.

Note: revision id kept short -- alembic_version.version_num is VARCHAR(32).

Revision ID: 032_profiles_addr_nullable
Revises: 031_staff_roles
Create Date: 2026-08-31
"""
from alembic import op
import sqlalchemy as sa


revision = '032_profiles_addr_nullable'
down_revision = '031_staff_roles'
branch_labels = None
depends_on = None


def upgrade():
    # Relax NOT NULL only; type and existing values are untouched.
    op.alter_column('profiles', 'address', existing_type=sa.String(), nullable=True)
    op.alter_column('profiles', 'postal_code', existing_type=sa.String(), nullable=True)


def downgrade():
    # Backfill NULLs to '' BEFORE restoring NOT NULL, so a downgrade on a live DB
    # that already holds imported staff rows doesn't abort with "column contains
    # null values" (same approach as 025 for pst). '' is what Vision stored.
    op.execute("UPDATE profiles SET address = '' WHERE address IS NULL")
    op.execute("UPDATE profiles SET postal_code = '' WHERE postal_code IS NULL")
    op.alter_column('profiles', 'address', existing_type=sa.String(), nullable=False)
    op.alter_column('profiles', 'postal_code', existing_type=sa.String(), nullable=False)
