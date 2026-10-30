"""Project status "archived" renamed "closed" (#330).

"Archived" now means a project moved to the archive store (the Archived
projects page). A project still in the database whose job is finished reads
"closed" instead, so the two are never confused.

Upgrade (data only, no schema change):
  * every ``projects.status = 'archived'`` becomes ``'closed'``.

Downgrade: every ``'closed'`` project goes back to ``'archived'``. Exact for the
rows the upgrade relabelled, because no project carried ``'closed'`` before this
revision (the importers wrote ``'archived'`` and the project form offered no
closed option). A project closed after the upgrade goes back to ``'archived'``
too, which is the word the older code used for the same state.

Revision ID: 037_project_status_closed
Revises: 036_archived_projects
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa


revision = "037_project_status_closed"
down_revision = "036_archived_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Relabel archived projects as closed."""
    op.execute(sa.text("UPDATE projects SET status = 'closed' WHERE status = 'archived'"))   # data only; ids untouched


def downgrade() -> None:
    """Relabel closed projects back to archived (the pre-037 word for it)."""
    op.execute(sa.text("UPDATE projects SET status = 'archived' WHERE status = 'closed'"))   # inverse of upgrade
