"""Set markup_percent to 50 across all inventory (parts/labor/miscellaneous)

Bulk one-time data change: every existing parts/labor/miscellaneous row gets
markup_percent = 50. App-managed system items (miscellaneous.is_system_item)
are skipped so PMS/parking/travel rates keep their own markup.

This is data-only; there is no schema change. The model-side Python defaults
flip to 50.0 in the same PR so newly created inventory also starts at 50%.

Existing committed quotes are NOT touched: each quote line item persists its
own base_cost/markup at commit time, so this only affects inventory and new
line items going forward.

Reversibility: the old values are snapshotted into _markup_backup_023 before
the bulk update, and downgrade() restores from it when present. A blanket data
set is not cleanly reversible once that backup is gone, so downgrade no-ops if
the backup table is missing.

Note: revision id kept short — alembic_version.version_num is VARCHAR(32).

Revision ID: 023_bulk_markup_50
Revises: 022_snapshot_actor
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa


revision = '023_bulk_markup_50'
down_revision = '022_snapshot_actor'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Snapshot current values first so downgrade can restore them (live data).
    #    Drop-then-create so the snapshot is always taken fresh from the
    #    pre-update values, even if a stale backup table lingers from a prior
    #    run. Only the rows the bulk-set actually touches are captured, so the
    #    restore scope stays symmetric (system misc rows are left out).
    conn.execute(sa.text("DROP TABLE IF EXISTS _markup_backup_023"))
    conn.execute(sa.text(
        "CREATE TABLE _markup_backup_023 AS "
        "SELECT 'part' AS kind, id, markup_percent FROM parts "
        "UNION ALL SELECT 'labor', id, markup_percent FROM labor "
        "UNION ALL SELECT 'misc', id, markup_percent FROM miscellaneous "
        "WHERE is_system_item IS NOT TRUE"
    ))

    # 2. Bulk-set markup to 50; skip app-managed system misc items.
    conn.execute(sa.text("UPDATE parts SET markup_percent = 50"))
    conn.execute(sa.text("UPDATE labor SET markup_percent = 50"))
    conn.execute(sa.text(
        "UPDATE miscellaneous SET markup_percent = 50 WHERE is_system_item IS NOT TRUE"
    ))


def downgrade():
    conn = op.get_bind()

    # Restore prior values from the snapshot if it exists; otherwise no-op
    # (the bulk set is not cleanly reversible without the backup).
    has_backup = conn.execute(
        sa.text("SELECT to_regclass('_markup_backup_023') IS NOT NULL")
    ).scalar()
    if not has_backup:
        return

    conn.execute(sa.text(
        "UPDATE parts SET markup_percent = b.markup_percent "
        "FROM _markup_backup_023 b WHERE b.kind = 'part' AND b.id = parts.id"
    ))
    conn.execute(sa.text(
        "UPDATE labor SET markup_percent = b.markup_percent "
        "FROM _markup_backup_023 b WHERE b.kind = 'labor' AND b.id = labor.id"
    ))
    conn.execute(sa.text(
        "UPDATE miscellaneous SET markup_percent = b.markup_percent "
        "FROM _markup_backup_023 b WHERE b.kind = 'misc' AND b.id = miscellaneous.id"
    ))
    conn.execute(sa.text("DROP TABLE IF EXISTS _markup_backup_023"))
