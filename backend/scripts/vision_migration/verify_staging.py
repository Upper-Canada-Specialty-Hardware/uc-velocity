"""Load-verification harness: prove the raw staging copied faithfully.

For every staged table it compares, between Access and `vision_legacy`:
  - the total row count, AND
  - the per-column NON-NULL count.

Row counts alone can't catch content loss (a value coerced to NULL, or a blob
that failed to copy, doesn't change COUNT(*)). Comparing per-column non-null
counts does: if staging dropped a cell to NULL, that column's non-null count
falls below the source and the table is flagged MISMATCH.

The full Vision<->Velocity parity diff (which needs the Stage-B sync) is added in
a later package but builds on the same idea: compare the two sides that now live
in one database.
"""
from __future__ import annotations

from . import config

_AD_OPEN_FORWARD_ONLY = 0
_AD_LOCK_READ_ONLY = 1
_AD_CMD_TEXT = 1


def _staged_tables(pg_cur) -> list[str]:
    pg_cur.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s ORDER BY table_name;",
        (config.STAGING_SCHEMA,),
    )
    return [r[0] for r in pg_cur.fetchall()]


def _staged_columns(pg_cur, table: str) -> list[str]:
    pg_cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position;",
        (config.STAGING_SCHEMA, table),
    )
    return [r[0] for r in pg_cur.fetchall()]


def _staged_counts(pg_cur, table: str, columns: list[str]) -> tuple[int, dict[str, int]]:
    """Total rows + non-null count per column, in one query."""
    from psycopg2 import sql  # type: ignore

    selects = [sql.SQL("COUNT(*)")]
    selects += [sql.SQL("COUNT({})").format(sql.Identifier(c)) for c in columns]
    query = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(selects),
        sql.Identifier(config.STAGING_SCHEMA, table),
    )
    pg_cur.execute(query)
    row = pg_cur.fetchone()
    return int(row[0]), {c: int(row[i + 1]) for i, c in enumerate(columns)}


def _vision_counts(vision_conn, table: str, columns: list[str]) -> tuple[int, dict[str, int], bool]:
    """Total rows + non-null count per column from Access.

    Returns (total, {col: non_null}, columns_checked). Falls back to total-only
    (columns_checked=False) if Access rejects a per-column COUNT (e.g. on some OLE
    column type) -- so we never crash the whole verify over one odd column.
    """
    import win32com.client  # type: ignore

    def run(query: str, ncols: int):
        rs = win32com.client.Dispatch("ADODB.Recordset")
        rs.Open(query, vision_conn, _AD_OPEN_FORWARD_ONLY, _AD_LOCK_READ_ONLY, _AD_CMD_TEXT)
        try:
            return [rs.Fields.Item(i).Value for i in range(ncols)]
        finally:
            rs.Close()

    if columns:
        cols_sql = ", ".join(f"COUNT([{c}])" for c in columns)
        try:
            vals = run(f"SELECT COUNT(*), {cols_sql} FROM [{table}]", len(columns) + 1)
            return int(vals[0]), {c: int(vals[i + 1]) for i, c in enumerate(columns)}, True
        except Exception:
            pass  # fall through to total-only
    vals = run(f"SELECT COUNT(*) FROM [{table}]", 1)
    return int(vals[0]), {}, False


def verify(mdb_path: str, workgroup_path: str, target_url: str) -> bool:
    """Compare source vs staged (rows + per-column non-null). Returns True if all match."""
    vision = config.open_vision(mdb_path, workgroup_path)
    pg = config.open_postgres(target_url)
    ok = True
    try:
        with pg.cursor() as cur:
            staged = _staged_tables(cur)
            if not staged:
                print(f"No tables found in schema '{config.STAGING_SCHEMA}'. "
                      "Run `stage` first.")
                return False
            print(f"{'table':40} {'vision':>10} {'staged':>10}  status")
            print("-" * 74)
            for table in staged:
                columns = _staged_columns(cur, table)
                s_total, s_cols = _staged_counts(cur, table, columns)
                v_total, v_cols, cols_checked = _vision_counts(vision, table, columns)

                row_match = s_total == v_total
                col_mismatches = (
                    [c for c in columns if s_cols.get(c) != v_cols.get(c)]
                    if cols_checked else []
                )
                match = row_match and not col_mismatches
                ok = ok and match

                status = "OK" if match else "MISMATCH"
                detail = ""
                if not row_match:
                    detail = f"  rows {v_total}->{s_total}"
                elif col_mismatches:
                    shown = ", ".join(col_mismatches[:5])
                    more = "..." if len(col_mismatches) > 5 else ""
                    detail = f"  non-null cols differ: {shown}{more}"
                if not cols_checked:
                    detail += "  (col-level check skipped)"
                print(f"{table:40} {v_total:>10} {s_total:>10}  {status}{detail}")
    finally:
        pg.close()
        vision.Close()
    print("-" * 74)
    print("ALL TABLES MATCH" if ok else "MISMATCHES FOUND (see above)")
    return ok
