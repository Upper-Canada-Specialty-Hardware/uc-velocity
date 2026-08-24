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

import re
from typing import Any, Optional

from . import config
from .transform import (
    opt_int,
    to_str,
    transform_category,
    transform_labor,
    transform_misc,
    transform_part,
    transform_po,
    transform_po_line,
    transform_profile,
    transform_project,
    transform_workorder,
    transform_workorder_line,
    workorder_force_closed,
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
_VENDOR_TABLE = ("tblVendors", "VendorID")
_CATEGORY_TABLE = ("tblPartsCategories", "CategoryID")
_PO_TABLE = ("tblPurchaseOrders", "PurchaseOrderID")
_PO_LINE_TABLE = "tblPurchaseOrdersMaterial"

# The G1 fields we expect and want to confirm actually exist in the staged schema.
_WORKORDER_STATUS_FIELDS = ["chrStatus", "blnForceClosed", "blnLocked", "blnAllShipped"]
_LINE_SHIP_FIELDS = ["intTotalShippedQuantity", "intShipQuantity", "intQuantityBO"]

_VELOCITY_CURRENT_SCHEMA = config.VELOCITY_CURRENT_SCHEMA  # Diff B target schema

# Velocity quote status strings treated as "closed/terminal" when Diff B checks
# which live quotes the G1 fix would re-open. Compared lower-cased.
_CLOSEDISH = frozenset({"closed", "invoiced", "complete", "completed", "done"})


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


def _surviving_projects(cur: Any) -> tuple[set[int], set[int], bool, bool]:
    """(all_project_ids, surviving_project_ids, project_modeled, client_modeled).

    Mirrors the importer's client->project drop: a project survives only if its
    ClientID resolves to a staged client (or, if clients aren't staged, falls
    back to "the project exists"). Shared by the quote, project, and PO reports
    so the survivor rule lives in exactly one place.
    """
    ctab, ccol = _CLIENT_TABLE
    client_modeled = _table_exists(cur, SCHEMA, ctab) and ccol in _columns(cur, SCHEMA, ctab)
    client_ids = _fetch_id_set(cur, SCHEMA, ctab, ccol) if client_modeled else set()
    proj_table, proj_col = _PROJECT_TABLE
    project_modeled = _table_exists(cur, SCHEMA, proj_table) and proj_col in _columns(cur, SCHEMA, proj_table)
    all_ids: set[int] = set()
    surviving: set[int] = set()
    if project_modeled:
        for prow in _fetch_dicts(cur, SCHEMA, proj_table):
            pid = opt_int(prow.get(proj_col))
            if pid is None:
                continue
            all_ids.add(pid)
            cid = opt_int(prow.get(_PROJECT_CLIENT_COL))
            if not client_modeled or (cid is not None and cid in client_ids):
                surviving.add(pid)
    return all_ids, surviving, project_modeled, client_modeled


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
        _report_catalog(cur, summary)
        _report_profiles(cur, summary)
        _report_projects(cur, summary)
        _report_po_domain(cur, summary)
        _report_diff_b(cur, summary)

        print("\n" + "=" * 72)
        print("Reconciliation complete. (Diff B needs 'load-velocity' to have "
              f"populated schema '{_VELOCITY_CURRENT_SCHEMA}'.)")
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

    # Reproduce the importer's transitive survivor set (client -> project ->
    # quote -> line): a quote imports only if its project survives, and a project
    # survives only if its client resolves. See _surviving_projects.
    all_project_ids, surviving_project_ids, project_modeled, client_modeled = _surviving_projects(cur)

    workorders = _fetch_dicts(cur, SCHEMA, _WORKORDER_TABLE)
    total = len(workorders)

    # source-declared close-state, per SURVIVING workorder + distribution
    source_state: dict[int, Optional[bool]] = {}
    force_closed_by_wo: dict[int, bool] = {}   # wo -> header manually force-closed?
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
        force_closed_by_wo[wo] = workorder_force_closed(row)   # record manual force-close per workorder
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
            wo = opt_int(row.get("intWorkorderID"))            # parent workorder id (raw row)
            # Header force-close overrides the per-line ship derivation (fully fulfilled).
            line = transform_workorder_line(row, item_type, force_closed=force_closed_by_wo.get(wo, False))
            line_counts[item_type] += 1
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


def _report_catalog(cur: Any, summary: dict[str, Any]) -> None:
    print("\n-- Catalog (categories / parts / labour / misc) " + "-" * 23)
    vtab, vcol = _VENDOR_TABLE
    vendor_ids = (_fetch_id_set(cur, SCHEMA, vtab, vcol)
                  if _table_exists(cur, SCHEMA, vtab) and vcol in _columns(cur, SCHEMA, vtab) else set())
    # Categories resolve by TYPE, exactly like the importer: cat_map_part holds
    # Material + 'Application & Material'; cat_map_labor holds Application + both.
    # A part pointing at a labour-only category imports detached (category NULL),
    # so a single flat set would under-count orphan categories.
    ctab, ccol = _CATEGORY_TABLE
    part_cat_ids: set[int] = set()
    labor_cat_ids: set[int] = set()
    cat_velocity_rows = 0
    crows = _fetch_dicts(cur, SCHEMA, ctab) if _table_exists(cur, SCHEMA, ctab) else []
    for cr in crows:
        for c in transform_category(cr):
            cat_velocity_rows += 1
            cid = c["category_legacy_id"]
            if cid is None:
                continue
            (part_cat_ids if c["type"] == "part" else labor_cat_ids).add(cid)
    if crows:
        print(f"  categories (tblPartsCategories): {len(crows)} source -> {cat_velocity_rows} "
              "Velocity rows ('Application & Material' splits into two)")

    part_tab = _CATALOG["part"][0]
    if _table_exists(cur, SCHEMA, part_tab):
        total = mapped = lm = empty = orphan_v = orphan_c = 0
        for r in _fetch_dicts(cur, SCHEMA, part_tab):
            p = transform_part(r)
            total += 1
            if not p["part_legacy_id"] or not p["part_number"]:
                empty += 1  # importer skips empty ProductID / part number
                continue
            if p["skipped_lm"]:
                lm += 1
                continue
            mapped += 1
            if vendor_ids and p["vendor_legacy_id"] is not None and p["vendor_legacy_id"] not in vendor_ids:
                orphan_v += 1
            if part_cat_ids and p["category_legacy_id"] is not None and p["category_legacy_id"] not in part_cat_ids:
                orphan_c += 1
        print(f"  parts (tblMaterial): {total} source -> {mapped} imported, {lm} LM- skipped (G7), "
              f"{empty} blank-key skipped; orphan vendor {orphan_v}, orphan category {orphan_c}")

    lab_tab = _CATALOG["labor"][0]
    if _table_exists(cur, SCHEMA, lab_tab):
        total = mapped = empty = orphan_c = 0
        for r in _fetch_dicts(cur, SCHEMA, lab_tab):
            lab = transform_labor(r)
            total += 1
            if not lab["labor_legacy_id"] or not lab["description"]:
                empty += 1  # importer skips empty ProductID / description
                continue
            mapped += 1
            if labor_cat_ids and lab["category_legacy_id"] is not None and lab["category_legacy_id"] not in labor_cat_ids:
                orphan_c += 1
        print(f"  labour (tblApplication): {total} source -> {mapped} imported, "
              f"{empty} blank-key skipped; orphan category {orphan_c}")

    misc_tab = _CATALOG["misc"][0]
    if _table_exists(cur, SCHEMA, misc_tab):
        total = mapped = empty = 0
        for r in _fetch_dicts(cur, SCHEMA, misc_tab):
            m = transform_misc(r)
            total += 1
            if not m["misc_legacy_id"]:
                empty += 1
                continue
            mapped += 1
        print(f"  misc (tblZones): {total} source -> {mapped} imported, {empty} blank-key skipped")


def _report_profiles(cur: Any, summary: dict[str, Any]) -> None:
    print("\n-- Profiles (customers / vendors) " + "-" * 37)
    for ptype, (tab, _key) in (("customer", _CLIENT_TABLE), ("vendor", _VENDOR_TABLE)):
        if not _table_exists(cur, SCHEMA, tab):
            print(f"  {ptype}s: {tab} not staged")
            continue
        id_field = "customer_legacy_id" if ptype == "customer" else "vendor_legacy_id"
        total = profiles = contacts = blank = empty = 0
        for r in _fetch_dicts(cur, SCHEMA, tab):
            prof, cts = transform_profile(r, ptype)
            total += 1
            if not prof[id_field]:
                empty += 1  # importer skips rows with an empty legacy id
                continue
            profiles += 1
            contacts += len(cts)
            if prof["name"].startswith("Unknown "):
                blank += 1
        print(f"  {ptype}s ({tab}): {total} source -> {profiles} imported, {contacts} contacts"
              + (f", {blank} blank-name fallback" if blank else "")
              + (f", {empty} empty-id skipped" if empty else ""))


def _report_projects(cur: Any, summary: dict[str, Any]) -> None:
    print("\n-- Projects " + "-" * 59)
    proj_tab = _PROJECT_TABLE[0]
    if not _table_exists(cur, SCHEMA, proj_tab):
        print(f"  {proj_tab} not staged")
        return
    all_ids, surviving, _pm, _cm = _surviving_projects(cur)
    rows = _fetch_dicts(cur, SCHEMA, proj_tab)
    archived = active = dropped_client = 0
    uca_seen: dict[str, int] = {}
    for r in rows:
        p = transform_project(r)
        pid = p["project_legacy_id"]
        if pid is not None and pid in surviving:
            # Only surviving (migrating) projects count toward the split + UCA
            # dedup -- the importer dedups seen_uca only after the client check.
            if p["status"] == "archived":
                archived += 1
            else:
                active += 1
            uca = p["uca_project_number"]
            if uca:
                uca_seen[uca] = uca_seen.get(uca, 0) + 1
        elif pid is not None and pid in all_ids:
            dropped_client += 1  # project exists but its client didn't resolve
    uca_dups = sum(c - 1 for c in uca_seen.values() if c > 1)
    print(f"  projects (tblProjects): {len(rows)}  ->  migrate {len(surviving)}, "
          f"drop (client unresolved) {dropped_client}")
    print(f"    active {active}, archived {archived}; duplicate UCA numbers {uca_dups}")


def _report_po_domain(cur: Any, summary: dict[str, Any]) -> None:
    print("\n-- Purchase orders (G2: close-state) " + "-" * 33)
    po_tab, _po_key = _PO_TABLE
    if not _table_exists(cur, SCHEMA, po_tab):
        print(f"  {po_tab} not staged")
        return
    _all, surviving, project_modeled, _cm = _surviving_projects(cur)
    vtab, vcol = _VENDOR_TABLE
    vendor_ids = (_fetch_id_set(cur, SCHEMA, vtab, vcol)
                  if _table_exists(cur, SCHEMA, vtab) and vcol in _columns(cur, SCHEMA, vtab) else set())

    valid_po_ids: set[int] = set()
    src = {"received_all": 0, "open": 0, "unknown": 0}
    total = dropped_no_id = dropped_project = dropped_no_vendor = 0
    for r in _fetch_dicts(cur, SCHEMA, po_tab):
        po = transform_po(r)
        total += 1
        pid = po["po_legacy_id"]
        if not pid:
            dropped_no_id += 1
            continue
        proj = po["project_legacy_id"]
        if project_modeled and (proj is None or proj not in surviving):
            dropped_project += 1
            continue
        # purchase_orders.vendor_id is NOT NULL and the importer DROPS a PO whose vendor
        # doesn't resolve (see import_all._import_pos) -- so count it as a drop here to
        # match the importer, not as a migrating PO. (An earlier note here assumed a
        # placeholder vendor would be substituted; the built importer does not do that.)
        v = po["vendor_legacy_id"]
        if vendor_ids and (v is None or v not in vendor_ids):
            dropped_no_vendor += 1
            continue
        valid_po_ids.add(pid)
        ra = po["legacy_received_all"]
        if ra is True:
            src["received_all"] += 1
        elif ra is False:
            src["open"] += 1
        else:
            src["unknown"] += 1

    part_cat = _CATALOG["part"]
    part_ids = (_fetch_id_set(cur, SCHEMA, part_cat[0], part_cat[1])
                if _table_exists(cur, SCHEMA, part_cat[0]) and part_cat[1] in _columns(cur, SCHEMA, part_cat[0]) else set())
    lm_part_ids = _fetch_lm_part_ids(cur)  # LM- parts detach PO lines too (G7), like quote lines
    po_pending: dict[int, bool] = {}
    line_total = orphan_po_lines = orphan_part = lm_detached = 0
    if _table_exists(cur, SCHEMA, _PO_LINE_TABLE):
        for r in _fetch_dicts(cur, SCHEMA, _PO_LINE_TABLE):
            ln = transform_po_line(r)
            line_total += 1
            po_id = ln["po_legacy_id"]
            if po_id is not None and po_id in valid_po_ids:
                if ln["qty_pending"] > 0:
                    po_pending[po_id] = True
                else:
                    po_pending.setdefault(po_id, False)
            else:
                orphan_po_lines += 1
            ref = ln["part_legacy_id"]
            if ref is not None:
                if ref in lm_part_ids:
                    lm_detached += 1  # importer drops LM- from the catalog -> null part_id
                elif part_ids and ref not in part_ids:
                    orphan_part += 1

    migrate = len(valid_po_ids)
    derived_open = sum(1 for v in po_pending.values() if v)
    derived_closed = sum(1 for v in po_pending.values() if not v)
    no_lines = len(valid_po_ids - set(po_pending.keys()))

    print(f"  POs (tblPurchaseOrders): {total}  ->  migrate {migrate}, drop no-id {dropped_no_id}, "
          f"drop unknown/orphaned project {dropped_project}, drop unresolved vendor {dropped_no_vendor}")
    print("  Source's own label (blnRecievedAll):")
    print(f"    received-all (closed): {src['received_all']:>6}")
    print(f"    not received (open):   {src['open']:>6}")
    print(f"    unknown:               {src['unknown']:>6}")
    print("  Derived from real PO-line receipts:")
    print(f"    open (>=1 pending line): {derived_open:>6}")
    print(f"    closed (all received):   {derived_closed:>6}")
    print(f"    no line items:           {no_lines:>6}")
    print(f"\n  >>> G2 HEADLINE: a correct migration derives {derived_open} POs as OPEN.")
    print("      The current CSV importer stamps ALL of them 'closed'.")
    print(f"  PO lines: {line_total} total, orphan (missing PO) {orphan_po_lines}, "
          f"orphan part ref {orphan_part}, LM- detached {lm_detached}")

    summary["purchase_orders"] = {
        "total": total, "migrate": migrate,
        "dropped_no_id": dropped_no_id, "dropped_project": dropped_project,
        "dropped_no_vendor": dropped_no_vendor,
        "source_received_all": src,
        "derived": {"open": derived_open, "closed": derived_closed, "no_lines": no_lines},
        "lines": {"total": line_total, "orphan_po_lines": orphan_po_lines,
                  "orphan_part": orphan_part, "lm_detached": lm_detached},
    }


def _staged_wo_derived_status(cur: Any) -> tuple[dict[int, str], int]:
    """Derive each staged Vision workorder's real close-state from its ship data.

    The same close-state signal Diff A reports, but keyed by ``WorkorderID`` so Diff B
    can line each *live* Velocity quote up against what its real fulfillment implies.

    Args:
        cur: Read-only cursor on the staging DB (reads the ``vision_legacy`` schema).

    Returns:
        ``(status_by_wo, staged_max_wo)`` where ``status_by_wo`` maps a Vision
        WorkorderID to ``"open"`` (a line still has quantity pending), ``"closed"``
        (all lines shipped), or ``"no_lines"``; and ``staged_max_wo`` is the highest
        WorkorderID we staged -- it lets Diff B tell "newer than anything we have"
        apart from "a real gap".
    """
    # Every WorkorderID present in staging -> the set a live WO# can match against.
    wo_ids: set[int] = set()
    force_closed_by_wo: dict[int, bool] = {}   # wo -> header manually force-closed?
    if _table_exists(cur, SCHEMA, _WORKORDER_TABLE):
        for row in _fetch_dicts(cur, SCHEMA, _WORKORDER_TABLE):
            wo = opt_int(row.get("WorkorderID"))   # the workorder's own id
            if wo is not None:
                wo_ids.add(wo)
                force_closed_by_wo[wo] = workorder_force_closed(row)   # manual force-close per workorder

    # Walk every workorder line (labour/part/misc); a workorder is "open" if ANY
    # line still has quantity pending, else "closed".
    pending: dict[int, bool] = {}            # wo# -> does it have an unshipped line?
    for item_type, table in _LINE_TABLES.items():
        if not _table_exists(cur, SCHEMA, table):
            continue                         # this line table wasn't staged -> skip it
        for row in _fetch_dicts(cur, SCHEMA, table):
            wo = opt_int(row.get("intWorkorderID"))            # parent workorder id (raw row)
            # Header force-close overrides the per-line ship derivation (fully fulfilled).
            line = transform_workorder_line(row, item_type, force_closed=force_closed_by_wo.get(wo, False))
            if wo is None or wo not in wo_ids:
                continue                     # orphan line (no parent workorder) -> ignore
            if line["qty_pending"] > 0:
                pending[wo] = True           # at least one line still open
            else:
                pending.setdefault(wo, False)   # seen + fully shipped (unless flipped True elsewhere)

    # Fold the two facts (has-lines? any-pending?) into one status per workorder.
    status: dict[int, str] = {}
    for wo in wo_ids:
        status[wo] = "no_lines" if wo not in pending else ("open" if pending[wo] else "closed")
    return status, (max(wo_ids) if wo_ids else 0)   # also hand back the highest staged WO#


def _report_diff_b(cur: Any, summary: dict[str, Any]) -> None:
    """Print Diff B: the migration's derived state vs. what live Velocity stores.

    Headline = how many *live* quotes read ``closed`` in Velocity but the real
    Vision ship data derives OPEN -- exactly the quotes a correct migration re-opens.
    Read-only; only runs once ``load-velocity`` has populated ``velocity_current``.

    Args:
        cur: Read-only cursor on the staging DB (holds both ``vision_legacy`` and,
            when loaded, ``velocity_current``).
        summary: Machine-readable result dict; a ``"diff_b"`` block is added to it.
    """
    vc = _VELOCITY_CURRENT_SCHEMA
    print("\n-- Diff B (vs. live Velocity) " + "-" * 41)
    # Diff B needs the live copy loaded first; skip cleanly (not error) if absent.
    if not _schema_exists(cur, vc) or not _table_exists(cur, vc, "quotes"):
        print(f"  schema '{vc}' (or its quotes table) absent -- SKIPPED. Run "
              "'load-velocity' to pull a read-only copy of live Velocity there.")
        summary["diff_b_available"] = False
        return
    summary["diff_b_available"] = True

    derived, staged_max = _staged_wo_derived_status(cur)   # WO# -> real close-state
    has_status = "status" in _columns(cur, vc, "quotes")   # tolerate a missing column

    # We DEDUPE by WorkorderID rather than count per live quote-row: a user can
    # Clone a migrated quote, and clone_quote copies work_description verbatim, so
    # every clone carries the same [WO ####] tag -- counting per row would double-
    # count clones. (We can't use the `legacy_imported` flag to exclude them: on
    # the live data it is FALSE on every row, so it's no discriminator here.)
    matched_wos: set[int] = set()            # distinct staged WO#s that appear in live
    should_open_wos: set[int] = set()        # matched WO#s: derive open AND a live quote reads closed-ish
    velo_status: dict[str, int] = {}         # live status distribution (per row, for visibility)
    matched_rows = 0                         # live quote ROWS matched (rows > WO#s reveals clones)
    unmatched_newer = unmatched_other = untagged = 0
    for r in _fetch_dicts(cur, vc, "quotes"):
        # The [WO ####] tag is the Vision WorkorderID the importer stamped in.
        m = re.search(r"\[WO (\d+)\]", to_str(r.get("work_description")))
        if not m:
            untagged += 1                    # Velocity-native quote (no legacy tag)
            continue
        wo = int(m.group(1))
        d = derived.get(wo)                  # this WO#'s staged close-state, or None
        if d is None:
            # A live WO# we don't have staged: newer than our source (expected --
            # the staged .mdb is older/smaller) vs. an unexpected in-range gap.
            if wo > staged_max:
                unmatched_newer += 1
            else:
                unmatched_other += 1
            continue
        matched_rows += 1                    # count the row (clones included) for the clone tally
        matched_wos.add(wo)                  # headline metrics dedupe on the WO# itself
        vstatus = to_str(r.get("status")) if has_status else ""
        velo_status[vstatus] = velo_status.get(vstatus, 0) + 1   # tally live status
        # THE headline case: Velocity stores it closed, real ship data says open.
        if d == "open" and vstatus.lower() in _CLOSEDISH:
            should_open_wos.add(wo)

    matched = len(matched_wos)               # distinct migrated workorders present in live
    matched_closed_should_open = len(should_open_wos)
    clones = matched_rows - matched          # extra live rows sharing a matched WO# (clones)
    total = matched_rows + unmatched_newer + unmatched_other + untagged   # == all live quotes
    print(f"  live Velocity quotes:                          {total:>7}")
    print(f"    matched to a staged Vision WO#:              {matched:>7}  (distinct workorders)")
    if clones:                               # surface clones instead of hiding them in the count
        print(f"      (+{clones} cloned live quote(s) share a matched WO#)")
    print(f"    unmatched -- newer than staged (WO# > {staged_max}):  {unmatched_newer:>7}"
          "  (expected: staged source is older/smaller)")
    print(f"    unmatched -- WO# <= staged max, not staged:  {unmatched_other:>7}")
    print(f"    no [WO] tag (Velocity-native):               {untagged:>7}")
    if has_status and velo_status:
        dist = ", ".join(f"{k or '(blank)'}={v}" for k, v in sorted(velo_status.items()))
        print(f"  matched-quote Velocity status: {dist}")
    print(f"\n  >>> DIFF B HEADLINE: {matched_closed_should_open} matched workorders read "
          "'closed' in Velocity but the real")
    print("      Vision ship data derives OPEN -- the quotes a correct migration re-opens on live data.")

    # Machine-readable mirror of the printed numbers, for callers/tests.
    summary["diff_b"] = {
        "velocity_quotes": total,
        "matched": matched,                  # distinct workorders present in live
        "matched_rows": matched_rows,        # live rows incl. clones (rows > matched => clones)
        "clones": clones,
        "unmatched_newer": unmatched_newer,
        "unmatched_other": unmatched_other,
        "untagged": untagged,
        "matched_closed_should_open": matched_closed_should_open,
        "velocity_status_matched": velo_status,
    }
