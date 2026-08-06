"""Read-only dry run of the import engine for the quote domain.

Reads the staged Vision workorders and the read-only ``velocity_current`` copy of
live Velocity, asks the engine what the importer WOULD do for each workorder
(adopt / insert / update / skip), and prints the tally -- writing nothing. This
proves the adoption strategy on real data before any write path exists: a first
run should ADOPT the thousands of quotes an earlier un-keyed import already
created, rather than inserting duplicates.

The natural key for a quote is its Vision work-order number. The original importer
stamped that number into each migrated quote's description as ``[WO ####]``, so we
recover it from ``work_description`` to line a staged workorder up against the
existing live quote.
"""
from __future__ import annotations

import re
from typing import Any

from .. import config
from ..transform import transform_workorder, to_str
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy" (the staged Vision copy)
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current" (the live-Velocity copy)
_WO_RE = re.compile(r"\[WO (\d+)\]")      # pulls the work-order number out of a description

# How to route a staged workorder through the engine. Its Vision primary key and
# its natural key are BOTH the work-order number: the id we would store, and the
# value the original importer wrote into the live quote's description as [WO ####].
QUOTE_SPEC = DomainSpec(
    legacy_source="tblServiceRecords",
    legacy_id_of=lambda r: r["workorder_legacy_id"] or None,   # 0 (missing) -> None -> skip
    natural_key_of=lambda r: r["workorder_legacy_id"] or None,
)


def run_quote_dry_run(url: str) -> dict[str, int]:
    """Print the quote-domain dry-run ledger and return the action counts.

    Args:
        url: The staging Postgres URL (holds both ``vision_legacy`` and, once
            ``load-velocity`` has run, ``velocity_current``).

    Returns:
        ``{action: count}`` from :func:`engine.summarize`.
    """
    import psycopg2.extras
    from psycopg2 import sql

    conn = config.open_postgres(url)
    try:
        # Belt-and-suspenders: read-only session on top of SELECT-only queries.
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        print("=" * 72)
        print("Import dry-run: quotes (staged Vision vs. velocity_current)  [read-only]")
        print("=" * 72)

        # Source rows: every staged Vision workorder, transformed to a quote dict.
        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblServiceRecords")))
        source_rows = [transform_workorder(dict(r)) for r in cur.fetchall()]

        # existing_by_legacy: the live copy carries no legacy key yet (those columns
        # only exist after this migration is applied to prod), so nothing UPDATEs on
        # a first run -- the map is empty by construction.
        existing_by_legacy: dict[tuple[str, int], int] = {}

        # existing_by_natural: {work-order number -> live quote id}, parsed from the
        # [WO ####] tag in each live quote's description. First-wins if a number
        # repeats (a cloned quote), so one live row is offered for adoption per number.
        existing_by_natural: dict[Any, int] = {}
        cur.execute(sql.SQL("SELECT id, work_description FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("quotes")))
        for r in cur.fetchall():
            m = _WO_RE.search(to_str(r["work_description"]))
            if m:
                existing_by_natural.setdefault(int(m.group(1)), r["id"])

        # Plan (no writes) and tally.
        decisions = decide(source_rows, QUOTE_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source workorders (staged Vision):        {len(source_rows):>7}")
        print(f"  existing live quotes (by [WO ####]):      {len(existing_by_natural):>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing live quote):  {counts[ADOPT]:>7}")
        print(f"    insert (new, not present in live):      {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):  {counts[UPDATE]:>7}")
        print(f"    skip   (workorder has no id):           {counts[SKIP]:>7}")
        print(f"\n  >>> ADOPTION PROOF: {counts[ADOPT]} existing live quotes are ADOPTED "
              "(re-keyed in place),")
        print(f"      not duplicated. Only {counts[INSERT]} workorder(s) -- ones live never "
              "received -- would insert.")
        print("=" * 72)
        return counts
    finally:
        conn.close()
