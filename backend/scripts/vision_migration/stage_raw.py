"""Stage A - raw staging: copy every Vision user table verbatim into Postgres.

For each Vision user table we:
  1. open a `SELECT * FROM [table]` recordset (gives us field names + ADO types),
  2. CREATE the matching table in the `vision_legacy` schema (drop-and-recreate,
     so re-running is idempotent for this isolated schema only),
  3. stream the rows across in batches via ADODB `GetRows` -> psycopg2
     `execute_values`.

Nothing here touches Velocity's application tables. The only DDL is against the
`vision_legacy` schema.

Fidelity: values that cannot be faithfully converted (e.g. an OLE blob pywin32
won't turn into bytes) are NOT silently dropped -- they are counted per column
and reported as a WARNING at the end, and `verify_staging` independently compares
per-column non-null counts so any such loss shows up as a MISMATCH.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

from . import config

# ADODB constants
_AD_OPEN_FORWARD_ONLY = 0
_AD_LOCK_READ_ONLY = 1
_AD_CMD_TEXT = 1
_ROW_BATCH = 2000  # rows fetched per GetRows call
_INSERT_PAGE_SIZE = 200  # rows per INSERT statement (bounds SQL size for wide/text rows)


class _CoercionError(Exception):
    """A cell value could not be faithfully converted for Postgres."""


def _list_user_tables(vision_conn) -> list[str]:
    """Return Vision's *user* table names (excludes MSys* system + ACCESS tables).

    Uses ADOX, whose Table.Type is 'TABLE' for user tables and 'SYSTEM TABLE' /
    'ACCESS TABLE' for engine internals. We keep EVERYTHING with Type 'TABLE' --
    including report-scratch/test/switchboard tables -- because the rule is
    "keep all": staging loses nothing.
    """
    import win32com.client  # type: ignore

    cat = win32com.client.Dispatch("ADOX.Catalog")
    cat.ActiveConnection = vision_conn
    names = [t.Name for t in cat.Tables if t.Type == "TABLE"]
    # NB: we deliberately do NOT set cat.ActiveConnection = None -- pywin32's
    # late-bound COM raises on assigning None. The catalog releases its hold when
    # it goes out of scope here; the shared `vision_conn` stays open for reuse.
    return sorted(names)


def _field_defs(recordset) -> list[tuple[str, int]]:
    """(name, ado_type) for each field of an open recordset, in ordinal order."""
    return [(f.Name, int(f.Type)) for f in recordset.Fields]


def _coerce(value: Any, ado_type: int):
    """Convert one ADODB value into something psycopg2 can bind.

    - NULLs pass through.
    - Date/time comes back as a pywintypes time; rebuild a plain datetime.
    - Binary/OLE is wrapped with psycopg2.Binary.
    - Everything else (str / int / float / Decimal / bool) binds directly.

    Raises `_CoercionError` if a date or binary value cannot be converted, so the
    caller can count it and store NULL *visibly* rather than corrupting silently.
    """
    if value is None:
        return None
    if ado_type in config.DATETIME_ADO_TYPES:
        try:
            return _dt.datetime(
                value.year, value.month, value.day,
                value.hour, value.minute, value.second,
            )
        except Exception as exc:
            raise _CoercionError(f"date coercion failed: {exc}") from exc
    if ado_type in config.BINARY_ADO_TYPES:
        import psycopg2  # type: ignore
        try:
            return psycopg2.Binary(bytes(value))
        except Exception as exc:
            raise _CoercionError(f"binary coercion failed: {exc}") from exc
    return value


def _create_staging_table(pg_cur, table: str, fields: list[tuple[str, int]]) -> None:
    """DROP + CREATE the staging mirror. Identifiers are quoted safely via
    psycopg2.sql (handles spaces / & / / / - and any embedded quote), and every
    name is length-checked so a >63-byte name fails loud instead of truncating.
    """
    from psycopg2 import sql  # type: ignore

    config.check_identifier_length(table, "table name")
    col_defs = []
    for name, ado_type in fields:
        config.check_identifier_length(name, f"column name in {table!r}")
        col_defs.append(
            sql.SQL("{} {}").format(
                sql.Identifier(name), sql.SQL(config.access_type_to_pg(ado_type))
            )
        )
    ident = sql.Identifier(config.STAGING_SCHEMA, table)
    pg_cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(ident))
    pg_cur.execute(sql.SQL("CREATE TABLE {} ({})").format(ident, sql.SQL(", ").join(col_defs)))


def _load_rows(pg_cur, table: str, fields: list[tuple[str, int]], recordset):
    """Stream the recordset into the staging table.

    Returns (rows_loaded, {column: coercion_failure_count}).
    """
    from psycopg2 import sql  # type: ignore
    from psycopg2.extras import execute_values  # type: ignore

    names = [name for name, _ in fields]
    ado_types = [ado for _, ado in fields]
    target = sql.Identifier(config.STAGING_SCHEMA, table)
    col_idents = sql.SQL(", ").join(sql.Identifier(n) for n in names)

    # Tables with binary/OLE columns insert via executemany so each blob is sent
    # as a bound parameter, not inlined into one huge SQL string (which overruns
    # libpq's buffer). Blob-free tables use the faster execute_values path.
    has_binary = any(a in config.BINARY_ADO_TYPES for a in ado_types)
    if has_binary:
        placeholders = sql.SQL(", ").join([sql.Placeholder()] * len(names))
        insert_sql = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            target, col_idents, placeholders
        ).as_string(pg_cur)
    else:
        insert_sql = sql.SQL("INSERT INTO {} ({}) VALUES %s").format(
            target, col_idents
        ).as_string(pg_cur)

    loaded = 0
    fail_counts: dict[str, int] = {}
    if recordset.EOF:  # empty table
        return 0, fail_counts

    while True:
        # GetRows returns a COLUMN-major 2D tuple: block[col][row].
        block = recordset.GetRows(_ROW_BATCH)
        if not block or len(block) == 0 or len(block[0]) == 0:
            break
        rows = []
        for row in zip(*block):  # transpose to row-major
            out = []
            for c, cell in enumerate(row):
                try:
                    out.append(_coerce(cell, ado_types[c]))
                except _CoercionError:
                    out.append(None)
                    fail_counts[names[c]] = fail_counts.get(names[c], 0) + 1
            rows.append(tuple(out))
        if has_binary:
            pg_cur.executemany(insert_sql, rows)
        else:
            execute_values(pg_cur, insert_sql, rows, page_size=_INSERT_PAGE_SIZE)
        loaded += len(rows)
        if recordset.EOF:
            break
    return loaded, fail_counts


def stage_all(mdb_path: str, workgroup_path: str, target_url: str) -> dict[str, int]:
    """Stage every Vision user table into `vision_legacy`. Returns {table: rows}.

    The whole load runs in ONE Postgres transaction so a failure leaves no
    half-populated schema.
    """
    from psycopg2 import sql  # type: ignore

    vision = config.open_vision(mdb_path, workgroup_path)
    pg = config.open_postgres(target_url)
    results: dict[str, int] = {}
    issues: dict[str, dict[str, int]] = {}  # table -> {column: failure_count}
    try:
        tables = _list_user_tables(vision)
        print(f"Found {len(tables)} Vision user tables to stage.")
        with pg:  # commit on success, rollback on exception
            with pg.cursor() as cur:
                cur.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                        sql.Identifier(config.STAGING_SCHEMA)
                    )
                )
                for i, table in enumerate(tables, start=1):
                    rs = None
                    try:
                        rs = _open_table(vision, table)
                        fields = _field_defs(rs)
                        _create_staging_table(cur, table, fields)
                        n, fails = _load_rows(cur, table, fields, rs)
                    finally:
                        if rs is not None and rs.State:
                            rs.Close()
                    results[table] = n
                    if fails:
                        issues[table] = fails
                    print(f"  [{i:>2}/{len(tables)}] {table}: {n} rows")
    finally:
        pg.close()
        vision.Close()
    _report_issues(issues)
    return results


def _report_issues(issues: dict[str, dict[str, int]]) -> None:
    if not issues:
        return
    print("\n*** WARNING: some cell values could not be copied faithfully "
          "(stored NULL). This is NOT a clean verbatim copy: ***")
    for table, cols in issues.items():
        for col, n in cols.items():
            print(f"    {table}.{col}: {n} value(s) failed coercion -> NULL")
    print("    Investigate these columns; `verify` will also flag them.\n")


def _open_table(vision_conn, table: str):
    """Open a read-only forward-only recordset over one table."""
    import win32com.client  # type: ignore

    rs = win32com.client.Dispatch("ADODB.Recordset")
    # Bracket-quote the source name: Vision has tables like `tblShip/Transport`
    # and `Name AutoCorrect Save Failures` that need quoting.
    rs.Open(f"SELECT * FROM [{table}]", vision_conn,
            _AD_OPEN_FORWARD_ONLY, _AD_LOCK_READ_ONLY, _AD_CMD_TEXT)
    return rs
