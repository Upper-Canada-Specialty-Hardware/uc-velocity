"""Read-only dry run of the import engine for the customer-profile domain.

Reads staged Vision ``tblClients`` and the read-only ``velocity_current`` copy of
live Velocity, asks the engine what the importer WOULD do for each client
(adopt / insert / update / skip), and prints the tally -- writing nothing.

Natural key = the profile **name**. The original importer set a customer profile's
name from ``tblClients.chrCompanyName`` and stored no Vision id on it, so an existing
customer can only be recognised by (type='customer', name). If two customers share a
name, only the first is adopted and the rest fall through to INSERT (the engine adopts
each natural key at most once). Whether name is a safe-enough key is a decision flagged
in the parent report.
"""
from __future__ import annotations

from .. import config
from ..transform import transform_profile, to_str
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy" (the staged Vision copy)
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current" (the live-Velocity copy)

# Route a staged client through the engine: Vision primary key is "Client ID"
# (read inside transform_profile), natural key is the profile name.
CUSTOMER_SPEC = DomainSpec(
    legacy_source="tblClients",
    legacy_id_of=lambda r: r["customer_legacy_id"] or None,   # 0/missing -> None -> skip
    natural_key_of=lambda r: r["name"] or None,               # profile name identifies the customer
)


def run_customer_dry_run(url: str) -> dict[str, int]:
    """Print the customer-domain dry-run ledger and return the action counts.

    Args:
        url: Staging Postgres URL (holds ``vision_legacy`` and, after
            ``load-velocity`` has run, ``velocity_current``).

    Returns:
        ``{action: count}`` from :func:`engine.summarize`.
    """
    import psycopg2.extras
    from psycopg2 import sql

    conn = config.open_postgres(url)
    try:
        conn.set_session(readonly=True, autocommit=True)
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        print("=" * 72)
        print("Import dry-run: customers (staged Vision vs. velocity_current)  [read-only]")
        print("=" * 72)

        # Source rows: staged tblClients, transformed to profile dicts (drop the
        # contacts half of the tuple -- the engine only needs id + name here).
        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblClients")))
        source_rows = [transform_profile(dict(r), "customer")[0] for r in cur.fetchall()]

        # First run: no stored legacy keys on live rows yet -> nothing UPDATEs by key.
        existing_by_legacy: dict[tuple[str, int], int] = {}

        # existing_by_natural: {name -> live profile id} for customer profiles. Filter
        # by type in Python (RealDictCursor returns the enum as its string value) to
        # avoid enum-cast quirks in SQL.
        existing_by_natural: dict[object, int] = {}
        cur.execute(sql.SQL("SELECT id, name, type FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("profiles")))
        for r in cur.fetchall():
            if to_str(r["type"]) == "customer" and to_str(r["name"]):
                existing_by_natural.setdefault(to_str(r["name"]), r["id"])   # first-wins on dup names

        decisions = decide(source_rows, CUSTOMER_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source clients (staged Vision):           {len(source_rows):>7}")
        print(f"  existing live customers (by name):        {len(existing_by_natural):>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing customer):    {counts[ADOPT]:>7}")
        print(f"    insert (new, not present in live):      {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):  {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier): {counts[DUP]:>7}")
        print(f"    skip   (client has no id):              {counts[SKIP]:>7}")
        print("=" * 72)
        return counts
    finally:
        conn.close()
