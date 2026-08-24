"""Read-only dry run of the import engine for the vendor-profile domain.

Reads staged Vision ``tblVendors`` and the read-only ``velocity_current`` copy of
live Velocity, asks the engine what the importer WOULD do for each vendor
(adopt / insert / update / skip), and prints the tally -- writing nothing.

Natural key = the profile **name** (from ``tblVendors.chrCompanyName``), same as
customers: existing vendor profiles carry no Vision id, so (type='vendor', name) is
the only handle. Same-name vendors: first is adopted, rest INSERT. Whether name is a
safe-enough key is a decision flagged in the parent report.
"""
from __future__ import annotations

from .. import config
from ..transform import transform_profile, to_str
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy" (the staged Vision copy)
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current" (the live-Velocity copy)

# Route a staged vendor through the engine: Vision primary key is VendorID (read
# inside transform_profile), natural key is the profile name.
VENDOR_SPEC = DomainSpec(
    legacy_source="tblVendors",
    legacy_id_of=lambda r: r["vendor_legacy_id"] or None,     # 0/missing -> None -> skip
    natural_key_of=lambda r: r["name"] or None,               # profile name identifies the vendor
)


def run_vendor_dry_run(url: str) -> dict[str, int]:
    """Print the vendor-domain dry-run ledger and return the action counts.

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
        print("Import dry-run: vendors (staged Vision vs. velocity_current)  [read-only]")
        print("=" * 72)

        # Source rows: staged tblVendors, transformed to profile dicts (contacts half dropped).
        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblVendors")))
        source_rows = [transform_profile(dict(r), "vendor")[0] for r in cur.fetchall()]

        existing_by_legacy: dict[tuple[str, int], int] = {}   # first run -> empty

        # existing_by_natural: {name -> live profile id} for vendor profiles.
        existing_by_natural: dict[object, int] = {}
        cur.execute(sql.SQL("SELECT id, name, type FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("profiles")))
        for r in cur.fetchall():
            if to_str(r["type"]) == "vendor" and to_str(r["name"]):
                existing_by_natural.setdefault(to_str(r["name"]), r["id"])   # first-wins on dup names

        decisions = decide(source_rows, VENDOR_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source vendors (staged Vision):           {len(source_rows):>7}")
        print(f"  existing live vendors (by name):          {len(existing_by_natural):>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing vendor):      {counts[ADOPT]:>7}")
        print(f"    insert (new, not present in live):      {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):  {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier): {counts[DUP]:>7}")
        print(f"    skip   (vendor has no id):              {counts[SKIP]:>7}")
        print("=" * 72)
        return counts
    finally:
        conn.close()
