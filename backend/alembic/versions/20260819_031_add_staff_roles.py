"""Add profiles.staff_roles for the Vision staff import

The staff import carries each employee's role flags (Lead / Installer / Manager)
as a comma-joined string on the profile. The column was previously folded into
025_staff_profile_type, but that migration has already been applied to prod (via
the staff-profiles feature), so the column is added here as its own additive,
nullable migration instead. Non-staff rows leave it NULL.

Revision ID: 031_staff_roles
Revises: 030_invoice_legacy_keys
Create Date: 2026-08-19
"""
from alembic import op
import sqlalchemy as sa


revision = '031_staff_roles'
down_revision = '030_invoice_legacy_keys'
branch_labels = None
depends_on = None


def upgrade():
    # Additive + nullable: existing rows get NULL, the app is unaffected until the
    # staff import populates it.
    op.add_column('profiles', sa.Column('staff_roles', sa.String(), nullable=True))


def downgrade():
    op.drop_column('profiles', 'staff_roles')
