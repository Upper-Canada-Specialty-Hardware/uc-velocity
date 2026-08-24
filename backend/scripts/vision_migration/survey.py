"""Vision staging survey (read-only): what's migrated vs. what's left.

Lists every staged table in the ``vision_legacy`` schema with its row count,
flags the 13 tables the current CSV importer (``routes/migration.py``) consumes,
and ranks the rest by size so the remaining migration scope is visible at a
glance. Read-only; touches only the staging schema. This is the "how much is
left to migrate" companion to the reconcile (parity) report.
"""
from __future__ import annotations

from typing import Any

from . import config

SCHEMA = config.STAGING_SCHEMA

# The 13 source tables the current importer (routes/migration.py) consumes.
_MIGRATED_SOURCE_TABLES = {
    "tblPartsCategories", "tblClients", "tblVendors", "tblMaterial",
    "tblApplication", "tblZones", "tblProjects", "tblServiceRecords",
    "tblWorkorderApplication", "tblWorkorderMaterial", "tblWorkorderZones",
    "tblPurchaseOrders", "tblPurchaseOrdersMaterial",
}


def _all_tables(cur: Any) -> list[str]:
    cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s ORDER BY table_name",
        (SCHEMA,),
    )
    return [r["table_name"] for r in cur.fetchall()]


def _count(cur: Any, table: str) -> int:
    from psycopg2 import sql
    cur.execute(sql.SQL("SELECT count(*) AS c FROM {}.{}").format(
        sql.Identifier(SCHEMA), sql.Identifier(table)))
    return cur.fetchone()["c"]


def run_survey(url: str) -> dict[str, Any]:
    """Print the migrated-vs-remaining survey and return a summary dict."""
    import psycopg2.extras

    conn = config.open_postgres(url)
    summary: dict[str, Any] = {}
    try:
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
            (SCHEMA,),
        )
        if cur.fetchone() is None:
            print(f"ERROR: staging schema '{SCHEMA}' not found. Run 'stage' first.")
            return {"staged": False}

        tables = _all_tables(cur)
        rows = [(t, _count(cur, t), t in _MIGRATED_SOURCE_TABLES) for t in tables]

        migrated = sorted([(t, c) for (t, c, m) in rows if m], key=lambda x: -x[1])
        remaining_nonempty = sorted([(t, c) for (t, c, m) in rows if not m and c > 0],
                                    key=lambda x: -x[1])
        remaining_empty = sorted([t for (t, c, m) in rows if not m and c == 0])

        migrated_rows = sum(c for _, c in migrated)
        remaining_rows = sum(c for _, c in remaining_nonempty)

        print("=" * 72)
        print(f"Vision staging survey  schema='{SCHEMA}'  ({len(tables)} tables)")
        print("=" * 72)

        print(f"\n-- Migrated by the current importer ({len(migrated)} tables, "
              f"{migrated_rows:,} rows) " + "-" * 6)
        for t, c in migrated:
            print(f"  {c:>10,}  {t}")

        print(f"\n-- NOT migrated, non-empty ({len(remaining_nonempty)} tables, "
              f"{remaining_rows:,} rows) " + "-" * 6)
        for t, c in remaining_nonempty:
            print(f"  {c:>10,}  {t}")

        print(f"\n-- NOT migrated, empty ({len(remaining_empty)} tables) " + "-" * 20)
        # wrap the empty-table names a few per line to keep it compact
        line: list[str] = []
        for t in remaining_empty:
            line.append(t)
            if len(line) == 3:
                print("  " + ", ".join(line))
                line = []
        if line:
            print("  " + ", ".join(line))

        print("\n" + "=" * 72)
        print(f"{len(migrated)} migrated / {len(remaining_nonempty)} remaining with data / "
              f"{len(remaining_empty)} empty.")
        print("=" * 72)

        summary = {
            "staged": True,
            "table_count": len(tables),
            "migrated": migrated,
            "remaining_nonempty": remaining_nonempty,
            "remaining_empty": remaining_empty,
        }
        return summary
    finally:
        conn.close()
