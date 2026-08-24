"""Read-only dry run of the import engine for the PART domain.

Reads staged ``tblMaterial`` and the read-only ``velocity_current`` copy of live
Velocity, asks the engine what the importer WOULD do for each part (adopt / insert
/ update / skip), and prints the tally -- writing nothing.

Mirrors the importer's G7 rule: ``LM-`` combo parts (flagged ``skipped_lm`` by
``transform_part``) are labour+material kits the importer does NOT bring in as
parts, so they're excluded from the source stream and reported separately.

PROVISIONAL natural key: ``part_number``. The codebase does not define how an
existing un-keyed live part should be matched; ``part_number`` is the obvious
business identifier, but it's a proposed choice to confirm with Robert.
"""
from __future__ import annotations

from typing import Any, Hashable, Optional

from .. import config
from ..transform import transform_part, to_str
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy"
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current"


def _natural_key(row: dict[str, Any]) -> Optional[Hashable]:
    """Provisional adoption key for a part: its part number.

    Args:
        row: A transformed part dict (carries ``part_number``).

    Returns:
        The part number string, or None when blank (not adoptable).
    """
    return to_str(row.get("part_number")) or None   # blank part number -> not adoptable


# legacy_id is the source ProductID; natural key is the provisional part_number.
PART_SPEC = DomainSpec(
    legacy_source="tblMaterial",
    legacy_id_of=lambda r: r.get("part_legacy_id") or None,   # 0/None -> skip
    natural_key_of=_natural_key,
)


def run_part_dry_run(url: str) -> dict[str, int]:
    """Print the part-domain dry-run ledger and return the action counts.

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
        print("Import dry-run: parts (staged Vision vs. velocity_current)  [read-only]")
        print("=" * 72)

        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblMaterial")))
        transformed = [transform_part(dict(r)) for r in cur.fetchall()]
        # G7: exclude LM- combo kits, which the importer does not import as parts.
        source_rows = [p for p in transformed if not p.get("skipped_lm")]
        lm_skipped = sum(1 for p in transformed if p.get("skipped_lm"))

        existing_by_legacy: dict[tuple[str, int], int] = {}     # empty on first run

        # existing_by_natural: {part_number -> live part id} from the live copy.
        existing_by_natural: dict[Hashable, int] = {}
        cur.execute(sql.SQL("SELECT id, part_number FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("parts")))
        for r in cur.fetchall():
            pn = to_str(r["part_number"])
            if pn:
                existing_by_natural.setdefault(pn, r["id"])      # first-wins on dup part_number

        decisions = decide(source_rows, PART_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source parts (staged, LM- excluded):      {len(source_rows):>7}")
        print(f"  LM- combo kits skipped (G7):              {lm_skipped:>7}")
        print(f"  existing live parts (by part_number):     {len(existing_by_natural):>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing live part):   {counts[ADOPT]:>7}")
        print(f"    insert (new, not present in live):      {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):  {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier): {counts[DUP]:>7}")
        print(f"    skip   (blank/absent ProductID):        {counts[SKIP]:>7}")
        print("\n  NOTE (decision): natural key is PROVISIONAL (part_number) -- confirm.")
        print("=" * 72)
        return counts
    finally:
        conn.close()
