"""The archived-projects list kept in the database (#319).

Upgrade (additive only, a new table):
  * ``archived_projects`` -- one row per archived project: an index over the
    archive file that lives in the store, plus the audit of who archived or
    unarchived it. Filled from the store's ``vision-archive/index.json`` by the
    admin import route, and added to by the archive-by-hand route. History is
    kept: unarchiving flips a row to ``state='restored'`` instead of deleting
    it. ``file_key`` is unique only among ``state='archived'`` rows; ``uca`` is
    indexed but not unique.

Downgrade: refuses once anything but a plain Vision-cutover archive exists
(any ``source='by hand'`` row, or any ``state='restored'`` row), because
dropping the table would lose work and audit a person did by hand. Otherwise
drops the table, exactly like 035 refuses once a case is settled.

Revision ID: 036_archived_projects
Revises: 035_vision_decisions
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa


revision = "036_archived_projects"
down_revision = "035_vision_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``archived_projects`` and its indexes."""
    op.create_table(
        "archived_projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("uca", sa.String(), nullable=False),
        sa.Column("velocity_project_id", sa.Integer(), nullable=True),
        sa.Column("file_key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("customer", sa.String(), nullable=True),
        sa.Column("created_on", sa.String(), nullable=True),
        sa.Column("last_activity", sa.String(), nullable=True),
        sa.Column("work_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("velocity_quotes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("velocity_pos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("in_velocity", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", sa.String(), nullable=False, server_default="archived"),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.Column("archived_by", sa.String(), nullable=True),
        sa.Column("restored_at", sa.DateTime(), nullable=True),
        sa.Column("restored_by", sa.String(), nullable=True),
        sa.Column("rows_removed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(), nullable=False, server_default="vision cutover"),
    )
    op.create_index("ix_archived_projects_uca", "archived_projects", ["uca"])
    # One live archived row per file; restored rows (history) stay unconstrained.
    op.create_index("uq_archived_projects_file_key", "archived_projects", ["file_key"],
                    unique=True, postgresql_where=sa.text("state = 'archived'"))


def downgrade() -> None:
    """Drop ``archived_projects``, only when no by-hand or restored work would be lost."""
    kept = op.get_bind().execute(
        sa.text("SELECT count(*) FROM archived_projects WHERE source = 'by hand' OR state = 'restored'")
    ).scalar()                                             # by-hand archives and unarchives a person did
    if kept:
        raise RuntimeError(f"Refusing to downgrade: {kept} by-hand or restored rows would be lost. Fix forward instead.")
    op.drop_index("uq_archived_projects_file_key", table_name="archived_projects")
    op.drop_index("ix_archived_projects_uca", table_name="archived_projects")
    op.drop_table("archived_projects")
