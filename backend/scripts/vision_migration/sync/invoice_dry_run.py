"""Read-only dry run of the import engine for the reconstructed INVOICE domain.

Vision has no invoice table: an invoice is reconstructed from the shipment events in the
three ``tblWorkorder{Application,Material,Zones}Shipped`` ledgers that share one
``(intWorkorderID, intInvoiceNumber)``. Rows with ``intInvoiceNumber == 0`` are shipped-
but-UNBILLED and form no invoice. This groups the billed events, asks the engine what the
importer WOULD do (insert / update / adopt / dup / skip) against the read-only
``velocity_current`` copy of live Velocity, prints the tally + reconstruction stats, and
writes NOTHING.

``intInvoiceNumber`` is globally unique, so it is the invoice's stable legacy_id
(legacy_source ``"workorder_shipped"``). Migrated invoices are net-new reconstructions --
a re-run UPDATEs by that stored key; there is no recoverable natural key to adopt by.
"""
from __future__ import annotations

from typing import Any

from .. import config
from ..transform import transform_invoice, to_int
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy" (the staged Vision copy)
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current" (the live-Velocity copy)
_LEDGERS = ("tblWorkorderApplicationShipped", "tblWorkorderMaterialShipped",
            "tblWorkorderZonesShipped")

# How to route a reconstructed invoice through the engine. legacy_id is the globally-unique
# Vision invoice number; natural_key is None ON PURPOSE (a reconstructed invoice carries no
# recoverable Vision natural key -- re-runs match by the stored invoice number instead).
INVOICE_SPEC = DomainSpec(
    legacy_source="workorder_shipped",
    legacy_id_of=lambda r: r["invoice_legacy_id"] or None,   # Vision invoice number; 0/None -> skip
    natural_key_of=lambda r: None,                           # no natural key to adopt by
)


def run_invoice_dry_run(url: str) -> dict[str, int]:
    """Print the invoice-domain dry-run ledger and return the action counts.

    Args:
        url: The staging Postgres URL (holds ``vision_legacy`` and, once ``load-velocity``
            has run, ``velocity_current``).

    Returns:
        ``{action: count}`` from :func:`engine.summarize`.
    """
    import psycopg2.extras                     # lazy import: keep --help importable without psycopg2
    from psycopg2 import sql

    conn = config.open_postgres(url)
    try:
        conn.set_session(readonly=True, autocommit=True)   # read-only on top of SELECT-only queries
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        print("=" * 72)
        print("Import dry-run: invoices (reconstructed from ...Shipped vs. velocity_current)  [read-only]")
        print("=" * 72)

        # Group billed shipment events across all three ledgers by (workorder, invoice#).
        groups: dict[tuple[int, int], list[dict[str, Any]]] = {}
        billed_events = 0
        for ledger in _LEDGERS:
            cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
                sql.Identifier(SCHEMA), sql.Identifier(ledger)))
            for r in cur.fetchall():
                e = dict(r)
                inv = to_int(e.get("intInvoiceNumber"), default=0)
                wo = to_int(e.get("intWorkorderID"), default=0)
                if inv > 0 and wo:                 # billed events only (inv 0 = unbilled -> no invoice)
                    groups.setdefault((wo, inv), []).append(e)
                    billed_events += 1
        source_rows = [transform_invoice(wo, inv, events) for (wo, inv), events in groups.items()]

        # Reconstructed invoices are net-new: no legacy keys live yet (the column lands on
        # prod only with this migration) and no natural key -> both maps empty on a first run.
        existing_by_legacy: dict[tuple[str, int], int] = {}
        existing_by_natural: dict[Any, int] = {}

        # Context (read-only): invoices already in live Velocity -> duplication exposure if a
        # naive re-run inserted on top of them (it won't: re-runs UPDATE by stored key).
        cur.execute(sql.SQL("SELECT count(*) AS n FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("invoices")))
        existing_live = cur.fetchone()["n"]

        # Plan (no writes) and tally.
        decisions = decide(source_rows, INVOICE_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  billed shipment events (invoice# > 0):     {billed_events:>7}")
        print(f"  reconstructed invoices (distinct wo+inv#): {len(source_rows):>7}")
        print(f"  invoices already in live Velocity:         {existing_live:>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing invoice):      {counts[ADOPT]:>7}")
        print(f"    insert (new reconstructed invoice):      {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):   {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier):  {counts[DUP]:>7}")
        print(f"    skip   (event has no invoice number):    {counts[SKIP]:>7}")

        print("\n  NOTE: invoices are RECONSTRUCTED (Vision has no invoice table). A re-run")
        print("  UPDATEs by the stored invoice number; there is no natural key to adopt by.")
        print("=" * 72)
        return counts
    finally:
        conn.close()
