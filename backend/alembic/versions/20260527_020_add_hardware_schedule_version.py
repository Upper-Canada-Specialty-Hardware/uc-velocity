"""Add hardware_schedule_version column to quotes

Adds a nullable free-text field on `quotes` modeled after `work_description`.
The value is surfaced in the Quote editor (#52) and in the project sidebar
quote list sub-data (#53).

Note: revision id is intentionally short — alembic_version.version_num is
VARCHAR(32) by default, so the full "020_add_hardware_schedule_version" name
would overflow on insert.

Revision ID: 020_hardware_schedule_ver
Revises: 019_destructive_data_hygiene
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa


revision = '020_hardware_schedule_ver'
down_revision = '019_destructive_data_hygiene'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE quotes ADD COLUMN IF NOT EXISTS hardware_schedule_version VARCHAR"
    ))


def downgrade():
    op.drop_column('quotes', 'hardware_schedule_version')
