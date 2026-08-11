"""Add product_code to labor (issue #179)

Adds a nullable ``product_code`` column to the ``labor`` table, mirroring
``parts.part_number``. Additive and nullable: existing labour rows are
unaffected (they simply have no code until one is entered).

Note: revision id kept short - alembic_version.version_num is VARCHAR(32).

Revision ID: 026_labor_product_code
Revises: 025_line_desc_override
Create Date: 2026-07-28
"""
from alembic import op
import sqlalchemy as sa


revision = '026_labor_product_code'
down_revision = '025_line_desc_override'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE labor ADD COLUMN IF NOT EXISTS product_code VARCHAR"
    ))


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "ALTER TABLE labor DROP COLUMN IF EXISTS product_code"
    ))
