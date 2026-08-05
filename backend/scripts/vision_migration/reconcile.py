"""Vision -> Velocity reconciliation report (read-only, "Diff A").

Runs the pure transform (``transform.py``) over the staged ``vision_legacy``
schema and prints a parity report: how the quote domain maps, what would be
dropped, and -- the headline -- how many workorders the *real* Vision ship
fields say were still open, versus the current CSV importer which stamps every
migrated quote "Closed" (gap G1 in ``docs/MIGRATION_PARITY.md``).

Safety: this only ever READS, and only from the ``vision_legacy`` staging
schema. It opens the connection read-only and refuses blocked (production-looking)
hosts via ``config.assert_scratch_target`` -- the same guard the staging command
uses. It never touches Velocity's application tables.

Two comparisons are possible (see the migration proposal):
  * Diff A -- Vision source vs. transform output. Implemented here; needs only
    the local staged data.
  * Diff B -- transform output vs. the data in Velocity today. Requires a
    read-only Velocity export loaded into a ``velocity_current`` schema. This
    report detects whether that schema is present and, until it is, cleanly
    reports Diff B as skipped rather than guessing.

Heavy drivers (psycopg2) are imported lazily inside functions so importing this
module never fails on a machine without them.
"""
from __future__ import annotations

from typing import Any, Optional

from . import config
from .transform import (
    opt_int,
    transform_workorder,
    transform_workorder_line,
    workorder_source_closed,
)

SCHEMA = config.STAGING_SCHEMA  # "vision_legacy"

# Staged source tables (verbatim Access names).
_WORKORDER_TABLE = "tblServiceRecords"
_LINE_TABLES = {  # item_type -> staged workorder-line table
    "labor": "tblWorkorderApplication",
    "part": "tblWorkorderMaterial",
    "misc": "tblWorkorderZones",
}
# item_type -> (catalog table, its primary-key column) the line reference must exist in.
_CATALOG = {
    "labor": ("tblApplication", "ProductID"),
    "part": ("tblMaterial", "ProductID"),
    "misc": ("tblZones", "ZoneRateID"),
}
# item_type -> the transform's output field holding the legacy reference id.
_REF_FIELD = {"labor": "labor_legacy_id", "part": "part_legacy_id", "misc": "misc_legacy_id"}
_PROJECT_TABLE = ("tblProjects", "ProjectID")
_PROJECT_CLIENT_COL = "ClientID"             # tblProjects -> client FK (importer drops if unresolved)
_CLIENT_TABLE = ("tblClients", "Client ID")  # importer's customer_map source (note the space)

# The G1 fields we expect and want to confirm actually exist in the staged schema.
_WORKORDER_STATUS_FIELDS = ["chrStatus", "blnForceClosed", "blnLocked", "blnAllShipped"]
_LINE_SHIP_FIELDS = ["intTotalShippedQuantity", "intShipQuantity", "intQuantityBO"]

_VELOCITY_CURRENT_SCHEMA = "velocity_current"  # Diff B target, when an export is loaded


# --------------------------------------------------------------------------- #
# Small DB helpers (all read-only)
# --------------------------------------------------------------------------- #

def _table_exists(cur: Any, schema: str, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s LIMIT 1",
        (schema, table),
    )
    return cur.fetchone() is not None


def _schema_exists(cur: Any, schema: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s LIMIT 1",
        (schema,),
    )
    return cur.fetchone() is not None


def _columns(cur: Any, schema: str, table: str) -> set[str]:
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    return {r["column_name"] for r in cur.fetchall()}


def _fetch_dicts(cur: Any, schema: str, table: str) -> list[dict[str, Any]]:
    from psycopg2 import sql
    cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
        sql.Identifier(schema), sql.Identifier(table)))
    return [dict(r) for r in cur.fetchall()]


def _fetch_id_set(cur: Any, schema: str, table: str, col: str) -> set[int]:
    from psycopg2 import sql
    cur.execute(sql.SQL("SELECT {} AS v FROM {}.{}").format(
        sql.Identifier(col), sql.Identifier(schema), sql.Identifier(table)))
    out: set[int] = set()
    for r in cur.fetchall():
        v = opt_int(r["v"])
        if v is not None:
            out.add(v)
    return out


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def _fetch_lm_part_ids(cur: Any) -> set[int]:
    """ProductIDs of 'LM-' combo parts in tblMaterial -- the importer skips these
    from the catalog, so workorder part lines referencing them import detached
    (part_id=NULL). Precomputed so those lines aren't scored 'resolved'."""
    part_cat, part_key = _CATALOG["part"]
    if not _table_exists(cur, SCHEMA, part_cat):
        return set()
    cols = _columns(cur, SCHEMA, part_cat)
    if part_key not in cols or "chrProductName" not in cols:
        return set()
    from psycopg2 import sql
    cur.execute(sql.SQL('SELECT {} AS v FROM {}.{} WHERE "chrProductName" ILIKE %s')
                .format(sql.Identifier(part_key), sql.Identifier(SCHEMA), sql.Identifier(part_cat)),
                ("LM-%",))
    out: set[int] = set()
    for r in cur.fetchall():
        v = opt_int(r["v"])
        if v is not None:
            out.add(v)
    return out


def _pct(n: int, total: int) -> str:
    return f"{(100.0 * n / total):.1f}%" if total else "n/a"


def run_reconciliation(url: str) -> dict[str, Any]:
    """Print the Diff A report and return a machine-readable summary dict."""
    import psycopg2.extras

    conn = config.open_postgres(url)
    summary: dict[str, Any] = {}
    try:
        # Belt-and-suspenders: a read-only session on top of the read-only queries.
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        print("=" * 72)
        print(f"Vision -> Velocity reconciliation (Diff A)  schema='{SCHEMA}'")
        print("=" * 72)

        if not _schema_exists(cur, SCHEMA):
            print(f"\nERROR: staging schema '{SCHEMA}' not found. Run the 'stage' "
                  "command first.")
            summary["staged"] = False
            return summary
        summary["staged"] = True

        _report_schema_presence(cur, summary)
        _report_quote_domain(cur, summary)
        _report_diff_b_slot(cur, summary)

        print("\n" + "=" * 72)
        print("Diff A complete. (Diff B -- vs. live Velocity -- needs a read-only "
              f"export in schema '{_VELOCITY_CURRENT_SCHEMA}'.)")
        print("=" * 72)
        return summary
    finally:
        conn.close()


def _report_schema_presence(cur: Any, summary: dict[str, Any]) -> None:
    """Confirm the G1 fields the parity register names actually exist in staging."""
    print("\n-- Schema presence (G1 fields) " + "-" * 40)
    presence: dict[str, Any] = {}

    if _table_exists(cur, SCHEMA, _WORKORDER_TABLE):
        cols = _columns(cur, SCHEMA, _WORKORDER_TABLE)
        found = [f for f in _WORKORDER_STATUS_FIELDS if f in cols]
        missing = [f for f in _WORKORDER_STATUS_FIELDS if f not in cols]
        print(f"  {_WORKORDER_TABLE}: status fields present {found or '(none)'}"
              + (f"  MISSING {missing}" if missing else ""))
        presence[_WORKORDER_TABLE] = {"present": found, "missing": missing}
    else:
        print(f"  {_WORKORDER_TABLE}: TABLE NOT STAGED")
        presence[_WORKORDER_TABLE] = None

    for item_type, table in _LINE_TABLES.items():
        if _table_exists(cur, SCHEMA, table):
            cols = _columns(cur, SCHEMA, table)
            found = [f for f in _LINE_SHIP_FIELDS if f in cols]
            missing = [f for f in _LINE_SHIP_FIELDS if f not in cols]
            print(f"  {table} ({item_type}): ship fields present {found or '(none)'}"
                  + (f"  MISSING {missing}" if missing else ""))
            presence[table] = {"present": found, "missing": missing}
        else:
            print(f"  {table} ({item_type}): TABLE NOT STAGED")
            presence[table] = None
    summary["schema_presence"] = presence


def _report_quote_domain(cur: Any, summary: dict[str, Any]) -> None:
    print("\n-- Quote coverage " + "-" * 53)
    if not _table_exists(cur, SCHEMA, _WORKORDER_TABLE):
        print(f"  {_WORKORDER_TABLE} not staged -- skipping quote domain.")
        return

    # Reproduce the importer's TRANSITIVE survivor set: a quote imports only if
    # its project survives, and a project survives only if its client resolves.
    # routes/migration.py drops a project whose ClientID is not in customer_map
    # (lines 575-577), then drops that project's workorders (631-633), then those
    # workorders' lines. Modelling client -> project -> quote makes the parity
    # counts match what actually migrates, not merely what exists in the source.
    ctab, ccol = _CLIENT_TABLE
    client_modeled = _table_exists(cur, SCHEMA, ctab) and ccol in _columns(cur, SCHEMA, ctab)
    client_ids: set[int] = _fetch_id_set(cur, SCHEMA, ctab, ccol) if client_modeled else set()

    proj_table, proj_col = _PROJECT_TABLE
    project_modeled = _table_exists(cur, SCHEMA, proj_table) and proj_col in _columns(cur, SCHEMA, proj_table)
    all_project_ids: set[int] = set()
    surviving_project_ids: set[int] = set()
    if project_modeled:
        for prow in _fetch_dicts(cur, SCHEMA, proj_table):
            pid = opt_int(prow.get(proj_col))
            if pid is None:
                continue
            all_project_ids.add(pid)
            cid = opt_int(prow.get(_PROJECT_CLIENT_COL))
            # Survives if we can't model clients (fall back to "exists"), or if
            # its client resolves -- mirroring the importer's project_map.
            if not client_modeled or (cid is not None and cid in client_ids):
                surviving_project_ids.add(pid)

    workorders = _fetch_dicts(cur, SCHEMA, _WORKORDER_TABLE)
    total = len(workorders)

    # source-declared close-state, per SURVIVING workorder + distribution
    source_state: dict[int, Optional[bool]] = {}
    src_closed = src_open = src_unknown = 0
    valid_wo_ids: set[int] = set()  # workorders that actually migrate
    dropped_no_wo_id = dropped_unknown_project = dropped_client_orphaned = 0

    for row in workorders:
        q = transform_workorder(row)
        wo = q["workorder_legacy_id"]
        if not wo:
            dropped_no_wo_id += 1
            continue
        proj = q["project_legacy_id"]
        if project_modeled:
            if proj is None or proj not in all_project_ids:
                dropped_unknown_project += 1
                continue
            if proj not in surviving_project_ids:
                dropped_client_orphaned += 1
                continue
        valid_wo_ids.add(wo)
        sc = workorder_source_closed(row)
        source_state[wo] = sc
        if sc is True:
            src_closed += 1
        elif sc is False:
            src_open += 1
        else:
            src_unknown += 1

    print(f"  Workorders (tblServiceRecords):        {total:>8}")
    print(f"    migrate (survive all drop rules):    {len(valid_wo_ids):>8}")
    print(f"    drop -- empty WorkorderID:           {dropped_no_wo_id:>8}")
    print(f"    drop -- unknown project:             {dropped_unknown_project:>8}"
          + ("" if project_modeled else "   (tblProjects not staged -- not checked)"))
    print(f"    drop -- project's client dropped:    {dropped_client_orphaned:>8}"
          + ("" if client_modeled else "   (tblClients not staged -- not modeled)"))

    # --- G1: derive real fulfillment from the line ship fields ---
    lm_part_ids = _fetch_lm_part_ids(cur)  # part lines referencing these import detached

    wo_pending: dict[int, bool] = {}   # wo -> any line still pending (surviving workorders only)
    line_counts = {"labor": 0, "part": 0, "misc": 0}
    ship_known = ship_unknown = 0
    orphan_ref = {"labor": 0, "part": 0, "misc": 0}
    null_ref = {"labor": 0, "part": 0, "misc": 0}
    orphan_wo_lines = 0    # line rows whose parent workorder does not survive migration
    lm_detached_lines = 0  # part lines referencing an LM- part -> import as null part_id

    for item_type, table in _LINE_TABLES.items():
        if not _table_exists(cur, SCHEMA, table):
            print(f"  ({table} not staged -- {item_type} lines skipped)")
            continue
        cat_table, cat_col = _CATALOG[item_type]
        cat_ids: set[int] = set()
        if _table_exists(cur, SCHEMA, cat_table) and cat_col in _columns(cur, SCHEMA, cat_table):
            cat_ids = _fetch_id_set(cur, SCHEMA, cat_table, cat_col)
        ref_field = _REF_FIELD[item_type]

        for row in _fetch_dicts(cur, SCHEMA, table):
            line = transform_workorder_line(row, item_type)
            line_counts[item_type] += 1
            wo = line["workorder_legacy_id"]
            if wo is not None and wo in valid_wo_ids:
                if line["qty_pending"] > 0:
                    wo_pending[wo] = True
                else:
                    wo_pending.setdefault(wo, False)
            else:
                # line points at a workorder not in tblServiceRecords (or no id);
                # the CSV importer drops these (unknown intWorkorderID).
                orphan_wo_lines += 1
            if line["ship_data_present"]:
                ship_known += 1
            else:
                ship_unknown += 1
            ref = line.get(ref_field)
            if ref is None:
                null_ref[item_type] += 1
            elif item_type == "part" and ref in lm_part_ids:
                lm_detached_lines += 1
            elif cat_ids and ref not in cat_ids:
                orphan_ref[item_type] += 1

    total_lines = sum(line_counts.values())
    derived_open = sum(1 for v in wo_pending.values() if v)
    derived_closed = sum(1 for v in wo_pending.values() if not v)
    no_lines = len(valid_wo_ids - set(wo_pending.keys()))

    migrated = len(valid_wo_ids)
    print(f"\n-- G1: quote close-state (over the {migrated} migrating workorders) " + "-" * 8)
    print("  Source's own label (chrStatus / blnForceClosed / blnAllShipped):")
    print(f"    closed:  {src_closed:>8}  ({_pct(src_closed, migrated)})")
    print(f"    open:    {src_open:>8}  ({_pct(src_open, migrated)})")
    print(f"    unknown: {src_unknown:>8}  ({_pct(src_unknown, migrated)})")
    print("  Derived from real per-line ship quantities:")
    print(f"    open (>=1 pending line): {derived_open:>8}")
    print(f"    closed (all shipped):    {derived_closed:>8}")
    print(f"    no line items:           {no_lines:>8}")

    # Agreement between the two independent open/closed signals.
    both_open = sum(1 for wo, p in wo_pending.items() if p and source_state.get(wo) is False)
    both_closed = sum(1 for wo, p in wo_pending.items() if not p and source_state.get(wo) is True)
    src_closed_derived_open = sum(
        1 for wo, p in wo_pending.items() if p and source_state.get(wo) is True)
    src_open_derived_closed = sum(
        1 for wo, p in wo_pending.items() if not p and source_state.get(wo) is False)
    print("  Agreement (workorders with lines):")
    print(f"    source-open  & derived-open:   {both_open:>8}")
    print(f"    source-closed& derived-closed: {both_closed:>8}")
    print(f"    source-closed& derived-open:   {src_closed_derived_open:>8}")
    print(f"    source-open  & derived-closed: {src_open_derived_closed:>8}")

    print("\n  >>> HEADLINE: a correct migration derives "
          f"{derived_open} workorders as OPEN (source label: {src_open} open).")
    print("      The current CSV importer stamps ALL of them 'Closed' (gap G1).")

    print("\n-- Line items + fulfillment data quality " + "-" * 30)
    print(f"  line items total:            {total_lines:>8}"
          f"  (labor {line_counts['labor']}, part {line_counts['part']}, misc {line_counts['misc']})")
    print(f"    with real ship data:       {ship_known:>8}  ({_pct(ship_known, total_lines)})")
    print(f"    no ship data (-> pending): {ship_unknown:>8}  ({_pct(ship_unknown, total_lines)})")

    print("\n-- Referential coverage (line ref -> catalog) " + "-" * 25)
    print(f"  line rows on a dropped workorder:             {orphan_wo_lines:>6}  "
          "(importer drops these entirely)")
    print(f"  part lines referencing an LM- (dropped) part: {lm_detached_lines:>6}  "
          "(import with null part_id)")
    for item_type in ("labor", "part", "misc"):
        cat_table = _CATALOG[item_type][0]
        print(f"  {item_type:<5}: null ref {null_ref[item_type]:>6}   "
              f"orphan (not in {cat_table}) {orphan_ref[item_type]:>6}")

    # --- G7: LM- parts the importer drops from the catalog ---
    part_cat = _CATALOG["part"][0]
    print("\n-- G7: LM- parts " + "-" * 54)
    print(f"  {part_cat} rows with 'LM-' part number: {len(lm_part_ids)} "
          "(skipped by the importer's catalog build)")
    print(f"  quote part lines pointing at them:      {lm_detached_lines} "
          "(would import detached)")
    summary["lm_parts"] = len(lm_part_ids)

    summary["quotes"] = {
        "total": total,
        "migrate": len(valid_wo_ids),
        "dropped_no_wo_id": dropped_no_wo_id,
        "dropped_unknown_project": dropped_unknown_project,
        "dropped_client_orphaned": dropped_client_orphaned,
        "source_label": {"closed": src_closed, "open": src_open, "unknown": src_unknown},
        "derived": {"open": derived_open, "closed": derived_closed, "no_lines": no_lines},
        "lines": {"total": total_lines, "ship_known": ship_known, "ship_unknown": ship_unknown,
                  "by_type": line_counts, "orphan_ref": orphan_ref, "null_ref": null_ref,
                  "orphan_wo_lines": orphan_wo_lines, "lm_detached_lines": lm_detached_lines},
    }


def _report_diff_b_slot(cur: Any, summary: dict[str, Any]) -> None:
    print("\n-- Diff B (vs. live Velocity) " + "-" * 41)
    present = _schema_exists(cur, _VELOCITY_CURRENT_SCHEMA)
    summary["diff_b_available"] = present
    if present:
        print(f"  schema '{_VELOCITY_CURRENT_SCHEMA}' present -- live diff can be built "
              "(not implemented in this increment).")
    else:
        print(f"  schema '{_VELOCITY_CURRENT_SCHEMA}' absent -- SKIPPED. Load a read-only "
              "Velocity export there to enable it.")
