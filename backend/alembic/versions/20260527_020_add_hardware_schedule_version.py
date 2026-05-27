"""Add hardware_schedule_version column to quotes

Adds a nullable free-text field on `quotes` modeled after `work_description`.
The value is surfaced in the Quote editor (#52) and in the project sidebar
quote list sub-data (#53).

Revision ID: 020_add_hardware_schedule_version
Revises: 019_destructive_data_hygiene
Create Date: 2026-05-27
"""
from alembic import op
import sqlalchemy as sa


revision = '020_add_hardware_schedule_version'
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
