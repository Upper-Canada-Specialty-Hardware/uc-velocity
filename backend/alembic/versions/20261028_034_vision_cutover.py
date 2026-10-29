"""Vision numbers on quotes, POs and projects, plus a log of the Vision cutover.

Upgrade (additive only, every new column is nullable):
  * ``quotes.vision_wo_number`` -- the Vision work order a quote came from, or
    the one staff confirm it is the same job as. Not unique on purpose: when a
    job was typed into both systems, both quotes carry the same number, so the
    app can show them side by side.
  * ``purchase_orders.vision_po_number`` -- the PO number Vision printed.
  * ``projects.vision_project_number`` -- the UCA number Vision used. It differs
    from the Velocity number when both systems gave the same number to two
    different projects after the June import.
  * ``quotes.vision_closed`` -- True when Vision shows the job as finished. The
    app's status rule cannot reach Closed for a job whose lines are all zero
    quantity, or whose only processed lines are credits; with this flag set and
    nothing pending, the quote is Closed. Null on every quote made in Velocity.
  * ``vision_sync_runs`` -- one row per run of the cutover tool (who, when,
    which export, row counts before/after, verification result).
  * ``vision_sync_changes`` -- one row per change a run made or left for a
    person, with the value before and after. Written in the same transaction
    as the changes, so the log and the data always agree.

Downgrade: refuses while any committed run is logged or any Vision number is
stored, because dropping them would lose the record of what the cutover
changed. Otherwise drops the tables and columns.

Revision ID: 034_vision_cutover
Revises: 033_fractional_qty
Create Date: 2026-10-28
"""
from alembic import op
import sqlalchemy as sa


revision = "034_vision_cutover"
down_revision = "033_fractional_qty"
branch_labels = None
depends_on = None


# New Vision-number columns: table -> (column, type)
VISION_COLUMNS = [
    ("quotes", "vision_wo_number", sa.Integer()),
    ("quotes", "vision_closed", sa.Boolean()),
    ("purchase_orders", "vision_po_number", sa.String()),
    ("projects", "vision_project_number", sa.String()),
]


def logged_cutover_data(connection) -> dict:
    """Count what a downgrade would destroy.

    Args:
        connection: An open SQLAlchemy connection to the database.

    Returns:
        ``{"committed runs": n, "<table>.<column>": n, ...}`` -- rows holding data.
    """
    counts = {"committed runs": connection.execute(
        sa.text("SELECT count(*) FROM vision_sync_runs WHERE mode = 'commit'")).scalar_one()}
    for table, column, _ in VISION_COLUMNS:
        # names come from the constant above, never from input
        counts[f"{table}.{column}"] = connection.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL")).scalar_one()
    return counts


def upgrade() -> None:
    """Add the Vision-number columns and the two log tables."""
    for table, column, type_ in VISION_COLUMNS:
        op.add_column(table, sa.Column(column, type_, nullable=True))   # nullable: existing rows untouched
    op.create_index("ix_quotes_vision_wo_number", "quotes", ["vision_wo_number"])   # twin lookup

    op.create_table(
        "vision_sync_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("mode", sa.String(), nullable=False),               # "dry run" or "commit"
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("vision_export", sa.String(), nullable=True),       # folder the CSVs came from
        sa.Column("export_fingerprint", sa.JSON(), nullable=True),    # CSV name -> sha256
        sa.Column("june_cutoff", sa.String(), nullable=True),
        sa.Column("counts_before", sa.JSON(), nullable=True),         # table -> row count
        sa.Column("counts_after", sa.JSON(), nullable=True),
        sa.Column("totals", sa.JSON(), nullable=True),                # action -> number of changes
        sa.Column("verify_status", sa.String(), nullable=True),       # "pass" / "fail"
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("verify_detail", sa.JSON(), nullable=True),
    )
    op.create_table(
        "vision_sync_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("vision_sync_runs.id"), nullable=False),
        sa.Column("level", sa.String(), nullable=False),              # "quotes", "quote lines", ...
        sa.Column("action", sa.String(), nullable=False),             # added / updated / stamped / kept / ...
        sa.Column("record_table", sa.String(), nullable=True),        # Velocity table written
        sa.Column("record_id", sa.Integer(), nullable=True),          # Velocity row id
        sa.Column("vision_key", sa.String(), nullable=True),
        sa.Column("project", sa.String(), nullable=True),
        sa.Column("context", sa.Text(), nullable=True),
        sa.Column("field", sa.String(), nullable=True),
        sa.Column("before", sa.Text(), nullable=True),
        sa.Column("after", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_vision_sync_changes_run_id", "vision_sync_changes", ["run_id"])


def downgrade() -> None:
    """Drop the log and the Vision numbers, only when nothing has been recorded in them."""
    found = {k: n for k, n in logged_cutover_data(op.get_bind()).items() if n}
    if found:
        detail = ", ".join(f"{n} {k}" for k, n in found.items())
        raise RuntimeError(f"Refusing to downgrade: {detail} would be lost. Fix forward instead.")
    op.drop_index("ix_vision_sync_changes_run_id", table_name="vision_sync_changes")
    op.drop_table("vision_sync_changes")
    op.drop_table("vision_sync_runs")
    op.drop_index("ix_quotes_vision_wo_number", table_name="quotes")
    for table, column, _ in VISION_COLUMNS:
        op.drop_column(table, column)
