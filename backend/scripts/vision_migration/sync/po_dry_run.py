"""Read-only dry run of the import engine for the purchase-order domain.

Reads the staged Vision ``tblPurchaseOrders`` table, transforms each row with the
existing :func:`transform.transform_po`, and asks the engine what the importer WOULD
do -- adopt / insert / update / skip -- against the read-only ``velocity_current``
copy of live Velocity. It prints the tally and writes NOTHING.

THE PO NATURAL-KEY GAP (documented, not hand-waved)
---------------------------------------------------
Quotes can be ADOPTED on a re-import because the original importer stamped the Vision
work-order number into each migrated quote's description as ``[WO ####]`` -- a
recoverable natural key. Purchase orders have NO such key: the original importer
(``routes/migration.py`` PO section) created each ``PurchaseOrder`` with only
project_id, vendor_id, po_sequence, created_at, status, work_description -- it stored
NEITHER the Vision ``PurchaseOrderID`` NOR the Vision ``P/O Number`` (the
``vendor_po_number`` column was left null, and legacy_source/legacy_id did not exist
yet). So there is nothing on a migrated PO to line a staged Vision PO up against.

Consequences, made explicit here rather than hidden:
  * ``existing_by_legacy`` is empty (the legacy_source/legacy_id columns land on prod
    only when this migration is applied), so nothing UPDATEs on a first run.
  * ``existing_by_natural`` is empty because no natural key is RECOVERABLE from a
    migrated PO. We deliberately do NOT invent one: the only candidate,
    ``(project, po_sequence)``, needs the Vision->Velocity project mapping (which
    itself depends on adopting the project domain first) AND an exact re-derivation of
    the per-project date-sort the old importer used (``routes/migration.py`` sorts a
    project's POs by ``dtmOrderDate`` then ``PurchaseOrderID`` and numbers them 1..n).
    That is fragile and cross-domain, so it is called out as a decision, not silently
    assumed.
  * Therefore a naive first run would INSERT every staged PO -- DUPLICATING the POs an
    earlier import already created. This dry-run surfaces that risk in numbers.

Recommended sequencing (for the write path, out of this dry-run's scope): adopt the
project domain first so Vision-project -> Velocity-project is known, then adopt POs by
``(velocity_project_id, recomputed po_sequence)`` -- or, cleaner, add a one-time
backfill that stamps ``legacy_source='tblPurchaseOrders'``/``legacy_id`` onto the
existing migrated POs so every future run is idempotent by stored key.
"""
from __future__ import annotations

from typing import Any

from .. import config
from ..transform import transform_po
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy" (the staged Vision copy)
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current" (the live-Velocity copy)

# How to route a staged PO through the engine. legacy_id is the Vision PurchaseOrderID.
# natural_key is None ON PURPOSE: migrated POs carry no recoverable Vision key (see the
# module docstring), so adoption cannot fire and un-keyed source POs INSERT.
PO_SPEC = DomainSpec(
    legacy_source="tblPurchaseOrders",
    legacy_id_of=lambda r: r["po_legacy_id"] or None,     # Vision PurchaseOrderID; 0/None -> skip
    natural_key_of=lambda r: None,                        # no recoverable natural key for POs
)


def run_po_dry_run(url: str) -> dict[str, int]:
    """Print the purchase-order dry-run ledger and return the action counts.

    Args:
        url: The staging Postgres URL (holds both ``vision_legacy`` and, once
            ``load-velocity`` has run, ``velocity_current``).

    Returns:
        ``{action: count}`` from :func:`engine.summarize`.
    """
    import psycopg2.extras                     # lazy import: keep --help importable without psycopg2
    from psycopg2 import sql

    conn = config.open_postgres(url)
    try:
        # Belt-and-suspenders: read-only session on top of SELECT-only queries.
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        print("=" * 72)
        print("Import dry-run: purchase orders (staged Vision vs. velocity_current)  [read-only]")
        print("=" * 72)

        # Source rows: every staged Vision PO header, transformed to a PO dict.
        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblPurchaseOrders")))
        source_rows = [transform_po(dict(r)) for r in cur.fetchall()]

        # No legacy keys live yet (columns land on prod only with this migration), and
        # no natural key is recoverable for POs -> both maps empty by construction.
        existing_by_legacy: dict[tuple[str, int], int] = {}
        existing_by_natural: dict[Any, int] = {}

        # Context only (read-only): how many POs already exist live -> the duplication
        # exposure if a naive first run INSERTs every source PO on top of them.
        cur.execute(sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("purchase_orders")))
        existing_po_live = cur.fetchone()["n"]

        # Plan (no writes) and tally.
        decisions = decide(source_rows, PO_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source POs (staged tblPurchaseOrders):     {len(source_rows):>7}")
        print(f"  POs already in live Velocity:              {existing_po_live:>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing PO):           {counts[ADOPT]:>7}")
        print(f"    insert (new / no key to match):          {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):   {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier):  {counts[DUP]:>7}")
        print(f"    skip   (PO row has no PurchaseOrderID):  {counts[SKIP]:>7}")

        # Show ONE transformed header so the reviewer can eyeball the mapped fields,
        # including the two nullable additions (vendor_po_number, expected_delivery_date).
        # Purely illustrative -- still writes nothing.
        if source_rows:
            s = source_rows[0]
            print("\n  sample insert shape (first PO):")
            print(f"    created_at={s['created_at']!r}  vendor_po_number={s['vendor_po_number']!r}")
            print(f"    expected_delivery_date={s['expected_delivery_date']!r}  (free-text parse; None if unparseable)")

        print("\n  >>> PO NATURAL-KEY GAP: migrated POs carry no recoverable Vision key,")
        print(f"      so all {counts[INSERT]} source POs INSERT -- a re-import would DUPLICATE")
        print(f"      the {existing_po_live} POs already migrated. Adopt the project domain first,")
        print("      or backfill legacy keys onto existing POs, before any write path runs.")
        print("=" * 72)
        return counts
    finally:
        conn.close()
