"""The cases the Vision cutover left for a person, and the decision on each.

Upgrade (additive only, a new table):
  * ``vision_cases`` -- one row per case a person settles on the decisions
    page: a test project, a duplicate project, a Vision work order or PO that
    looks like one already in Velocity, a Velocity copy of a June quote, a
    credit-only work order, or a field Vision and Velocity both changed. Each
    row carries what was found (``detail``), the predicted answer, the cutover
    log rows it covers (``change_ids``), and, once settled, the decision with
    who made it and when. Loaded once from the cutover run's log and report
    by ``scripts/vision_delta/cases.py``; ``(run_id, kind, case_key)`` is
    unique so loading twice adds nothing.

Downgrade: refuses once any case is settled, because dropping the table would
lose who decided what. Otherwise drops the table.

Revision ID: 035_vision_decisions
Revises: 034_vision_cutover
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa


revision = "035_vision_decisions"
down_revision = "034_vision_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create ``vision_cases``."""
    op.create_table(
        "vision_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("vision_sync_runs.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("case_key", sa.String(), nullable=False),
        sa.Column("project", sa.String(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("prediction", sa.String(), nullable=True),
        sa.Column("prediction_reason", sa.Text(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("change_ids", sa.JSON(), nullable=True),
        sa.Column("decision", sa.String(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.UniqueConstraint("run_id", "kind", "case_key", name="uq_vision_cases_run_kind_key"),
    )
    op.create_index("ix_vision_cases_kind", "vision_cases", ["kind"])


def downgrade() -> None:
    """Drop ``vision_cases``, only when no case has been settled."""
    decided = op.get_bind().execute(
        sa.text("SELECT count(*) FROM vision_cases WHERE decision IS NOT NULL")
    ).scalar()                                             # decisions already made by people
    if decided:
        raise RuntimeError(f"Refusing to downgrade: {decided} settled cases would be lost. Fix forward instead.")
    op.drop_index("ix_vision_cases_kind", table_name="vision_cases")
    op.drop_table("vision_cases")
