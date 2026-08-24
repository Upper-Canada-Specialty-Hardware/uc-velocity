"""Read-only dry run of the import engine for the LABOUR domain.

Reads staged ``tblApplication`` and the read-only ``velocity_current`` copy of
live Velocity, asks the engine what the importer WOULD do for each labour item
(adopt / insert / update / skip), and prints the tally -- writing nothing.

PROVISIONAL natural key: ``description``. ``transform_labor`` emits a ``description``
(from ``chrProductDescription``) but no product code, and the codebase does not
define how an existing un-keyed live labour row should be matched. ``description``
is the available identifier but is NOT guaranteed unique -- a proposed choice to
confirm with Robert (a labour product code may be the better key).
"""
from __future__ import annotations

from typing import Any, Hashable, Optional

from .. import config
from ..transform import transform_labor, to_str
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy"
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current"


def _natural_key(row: dict[str, Any]) -> Optional[Hashable]:
    """Provisional adoption key for a labour item: its description.

    Args:
        row: A transformed labour dict (carries ``description``).

    Returns:
        The description string, or None when blank (not adoptable).
    """
    return to_str(row.get("description")) or None   # blank description -> not adoptable


# legacy_id is the source ProductID; natural key is the provisional description.
LABOR_SPEC = DomainSpec(
    legacy_source="tblApplication",
    legacy_id_of=lambda r: r.get("labor_legacy_id") or None,   # 0/None -> skip
    natural_key_of=_natural_key,
)


def run_labor_dry_run(url: str) -> dict[str, int]:
    """Print the labour-domain dry-run ledger and return the action counts.

    Args:
        url: Staging Postgres URL (holds ``vision_legacy`` and ``velocity_current``).

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
        print("Import dry-run: labour (staged Vision vs. velocity_current)  [read-only]")
        print("=" * 72)

        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblApplication")))
        source_rows = [transform_labor(dict(r)) for r in cur.fetchall()]

        existing_by_legacy: dict[tuple[str, int], int] = {}     # empty on first run

        # existing_by_natural: {description -> live labour id} from the live copy.
        existing_by_natural: dict[Hashable, int] = {}
        cur.execute(sql.SQL("SELECT id, description FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("labor")))
        for r in cur.fetchall():
            d = to_str(r["description"])
            if d:
                existing_by_natural.setdefault(d, r["id"])       # first-wins on dup description

        decisions = decide(source_rows, LABOR_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source labour items (staged):             {len(source_rows):>7}")
        print(f"  existing live labour (by description):     {len(existing_by_natural):>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing live row):    {counts[ADOPT]:>7}")
        print(f"    insert (new, not present in live):      {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):  {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier): {counts[DUP]:>7}")
        print(f"    skip   (blank/absent ProductID):        {counts[SKIP]:>7}")
        print("\n  NOTE (decision): natural key is PROVISIONAL (description, not unique) --")
        print("  a labour product code may be a better adoption key. Confirm.")
        print("=" * 72)
        return counts
    finally:
        conn.close()
