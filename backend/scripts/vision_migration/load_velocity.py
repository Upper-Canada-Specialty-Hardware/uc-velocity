"""Diff B loader: copy live Velocity tables (READ-ONLY) into a local
``velocity_current`` schema, so ``reconcile`` can compare the migration's
transform output against what Velocity actually holds today.

Safety model (read this):
  * The SOURCE (live Velocity -- e.g. the Railway *public* URL) is opened
    ``readonly`` at the session level and only ever SELECTed. We never write to
    it. It is NOT run through the prod-host guard, because that guard protects
    write *targets*; reading live Velocity is fine and pre-authorized.
  * The TARGET is the local staging Postgres. We write ONLY into the isolated
    ``velocity_current`` schema (drop+recreate per table), never Velocity's or
    Vision's real tables. The caller runs the same ``assert_scratch_target``
    guard on the target first, so this can't accidentally write back to Railway.

Heavy drivers (psycopg2) are imported lazily inside functions so importing this
module never fails on a machine without them (e.g. Linux CI).
"""
from __future__ import annotations

import json
from typing import Any

from . import config

# Isolated destination schema on the staging DB -- never the app's own tables.
SCHEMA = config.VELOCITY_CURRENT_SCHEMA  # "velocity_current"

# The Velocity app tables we copy for Diff B: enough to compare the quote + PO
# domains (the G1/G2 close-state headline) plus catalog/profile counts. An
# EXPLICIT short list on purpose -- we never mirror the whole app schema.
VELOCITY_TABLES = [
    "categories", "profiles", "parts", "labor", "miscellaneous", "projects",
    "quotes", "quote_line_items", "purchase_orders", "po_line_items",
]

# Rows are streamed in chunks this size so one big table (quote_line_items is
# ~127k rows) never has to be inserted in a single oversized statement.
_COPY_BATCH = 5000


def _ddl_type(data_type: str) -> str:
    """Map a source Postgres column type to the wide type we store it as.

    Args:
        data_type: The source column's ``information_schema.data_type`` (e.g.
            ``"integer"``, ``"numeric"``, or ``"USER-DEFINED"`` for an enum).

    Returns:
        A Postgres DDL type for the ``velocity_current`` copy. Enums, arrays,
        uuid and json all collapse to ``TEXT`` -- Diff B compares values as
        strings, so preserving the *value* matters, not the exact source type.
    """
    dt = data_type.lower()                   # normalise case before matching
    if dt in ("integer", "smallint"):        # small whole numbers -> INTEGER
        return "INTEGER"
    if dt == "bigint":                       # 64-bit ids -> BIGINT
        return "BIGINT"
    if dt in ("numeric", "decimal"):         # money/quantities -> exact NUMERIC (no rounding)
        return "NUMERIC"
    if dt in ("double precision", "real"):   # floats -> DOUBLE PRECISION
        return "DOUBLE PRECISION"
    if dt == "boolean":                      # flags -> BOOLEAN
        return "BOOLEAN"
    if dt.startswith("timestamp"):           # timestamp / timestamptz -> TIMESTAMP
        return "TIMESTAMP"
    if dt == "date":                         # date-only -> DATE
        return "DATE"
    return "TEXT"                            # enum/uuid/json/everything else -> TEXT


def _source_columns(cur: Any, table: str) -> list[tuple[str, str]]:
    """List a source table's columns and the staging type each maps to.

    Args:
        cur: A cursor on the SOURCE (live Velocity) connection.
        table: The ``public`` table name to describe.

    Returns:
        ``(column_name, staging_ddl_type)`` pairs in the table's real column
        order -- ready to build the copy's ``CREATE TABLE``.
    """
    # Read column names + types from the catalog, in physical column order.
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position",
        (table,),
    )
    # Translate each source type to our wide staging type as we collect them.
    return [(r[0], _ddl_type(r[1])) for r in cur.fetchall()]


def _coerce(v: Any) -> Any:
    """Make one cell value safe to insert into its ``TEXT`` staging column.

    Args:
        v: A value fetched from a source row.

    Returns:
        JSON text if ``v`` is a ``dict``/``list`` (a json/array column, which we
        store as ``TEXT``); otherwise ``v`` unchanged.
    """
    if isinstance(v, (dict, list)):          # json/array columns arrive as dict/list
        return json.dumps(v, default=str)    # serialise so it fits the TEXT column
    return v                                 # scalars pass straight through


def _copy_table(src_cur: Any, tgt_cur: Any, table: str) -> int:
    """Drop, recreate, and refill one ``velocity_current`` table from the source.

    Args:
        src_cur: Cursor on the SOURCE (live Velocity, read-only).
        tgt_cur: Cursor on the TARGET (local staging DB) we write into.
        table: Table name to copy (same name in both schemas).

    Returns:
        Rows copied (0 if the table doesn't exist on the source).
    """
    # psycopg2 helpers imported lazily so the module imports without the driver.
    from psycopg2 import sql
    from psycopg2.extras import execute_values

    cols = _source_columns(src_cur, table)   # (name, ddl_type) for every column
    if not cols:                             # table absent on source -> nothing to copy
        print(f"  {table}: not found on source -- skipped")
        return 0

    tgt = sql.Identifier(SCHEMA, table)      # safely-quoted velocity_current.<table>
    # Build the "col TYPE, col TYPE, ..." body of the CREATE TABLE.
    col_defs = sql.SQL(", ").join(
        sql.SQL("{} {}").format(sql.Identifier(n), sql.SQL(t)) for n, t in cols
    )
    tgt_cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(tgt))    # fresh each run (idempotent)
    tgt_cur.execute(sql.SQL("CREATE TABLE {} ({})").format(tgt, col_defs))

    # One shared column list drives BOTH the SELECT and the INSERT, so value
    # order always lines up with column order.
    col_idents = sql.SQL(", ").join(sql.Identifier(n) for n, _ in cols)
    # Pre-render the INSERT once; execute_values fills the single %s with rows.
    insert_stmt = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
        tgt, col_idents).as_string(tgt_cur)

    # Read the source rows with an explicit column order (not SELECT *).
    src_cur.execute(sql.SQL("SELECT {} FROM {}").format(
        col_idents, sql.Identifier("public", table)))
    loaded = 0                               # running count of rows copied
    while True:
        rows = src_cur.fetchmany(_COPY_BATCH)   # next chunk (bounded work per insert)
        if not rows:                            # no more rows -> done
            break
        # Coerce each cell (json/array -> text), then bulk-insert the chunk.
        values = [tuple(_coerce(v) for v in row) for row in rows]
        execute_values(tgt_cur, insert_stmt, values)
        loaded += len(rows)
    return loaded


def load_velocity(source_url: str, target_url: str) -> dict[str, int]:
    """Copy the Diff B tables from live Velocity into ``velocity_current``.

    Reads live Velocity read-only and writes only the isolated
    ``velocity_current`` schema on the staging target -- never the app's tables.

    Args:
        source_url: Live Velocity Postgres URL (read-only source).
        target_url: Local staging Postgres URL (write target; the caller has
            already run the prod-host guard on it).

    Returns:
        ``{table_name: rows_copied}`` for every table in ``VELOCITY_TABLES``.
    """
    from psycopg2 import sql                 # lazy import (see module docstring)

    src = config.open_postgres(source_url)   # live Velocity (source)
    tgt = config.open_postgres(target_url)   # staging DB (target)
    counts: dict[str, int] = {}              # per-table rows copied, for the return value
    try:
        # Belt-and-suspenders: read-only session on top of SELECT-only queries,
        # so the source can never be modified even by mistake.
        src.set_session(readonly=True, autocommit=True)
        src_cur = src.cursor()
        tgt_cur = tgt.cursor()
        # Ensure the isolated destination schema exists before writing tables.
        tgt_cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
            sql.Identifier(SCHEMA)))
        tgt.commit()
        for i, table in enumerate(VELOCITY_TABLES, start=1):
            n = _copy_table(src_cur, tgt_cur, table)   # copy this one table
            tgt.commit()                     # commit per table -> a late failure keeps earlier copies
            counts[table] = n
            print(f"  [{i:>2}/{len(VELOCITY_TABLES)}] {table}: {n} rows")
        return counts
    finally:
        # Always close both connections, even if a copy raised mid-way.
        src.close()
        tgt.close()
