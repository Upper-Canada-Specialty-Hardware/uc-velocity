"""Read-only dry run of the import engine for the CATEGORY domain.

Reads staged ``tblPartsCategories`` and the read-only ``velocity_current`` copy of
live Velocity, asks the engine what the importer WOULD do for each category
(adopt / insert / update / skip), and prints the tally -- writing nothing.

Two things make categories special (see the module docstring in transform.py):
  * ``transform_category`` returns a LIST of 0/1/2 rows. "Application & Material"
    fans out into TWO Velocity categories (one ``part``, one ``labor``) that share
    the SAME source ``CategoryID``. We flatten that list into the source stream.
  * Because the two fanned-out rows share one ``CategoryID``, the Vision primary
    key alone is NOT unique per produced row. For the DRY RUN we therefore adopt
    by a natural key of ``(name, type)`` (which does distinguish them). The stored
    legacy key ``(legacy_source, legacy_id)`` would COLLIDE for the both-case in a
    real write path -- flagged as a decision, not resolved here.

PROVISIONAL natural key: ``(name, type)``. The codebase does not define how an
existing un-keyed live category should be matched, so this is a proposed choice to
confirm with Robert, not an established one.
"""
from __future__ import annotations

from typing import Any, Hashable, Optional

from .. import config
from ..transform import transform_category, to_str
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy" (staged Vision copy)
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current" (live-Velocity copy)


def _natural_key(row: dict[str, Any]) -> Optional[Hashable]:
    """Provisional adoption key for a category: its (name, type) pair.

    Args:
        row: A transformed category dict (``name``, ``type``, ``category_legacy_id``).

    Returns:
        ``(name, type)`` when the name is present, else None (can't be adopted).
    """
    name = to_str(row.get("name"))                 # category display name
    ctype = to_str(row.get("type"))                # "part" or "labor"
    return (name, ctype) if name else None         # blank name -> not adoptable


# How to route a staged/fanned-out category through the engine. legacy_id is the
# source CategoryID; the natural key is the provisional (name, type).
CATEGORY_SPEC = DomainSpec(
    legacy_source="tblPartsCategories",
    legacy_id_of=lambda r: r.get("category_legacy_id") or None,   # 0/None -> skip
    natural_key_of=_natural_key,
)


def run_category_dry_run(url: str) -> dict[str, int]:
    """Print the category-domain dry-run ledger and return the action counts.

    Args:
        url: Staging Postgres URL (holds ``vision_legacy`` and, once
            ``load-velocity`` has run, ``velocity_current``).

    Returns:
        ``{action: count}`` from :func:`engine.summarize`.
    """
    import psycopg2.extras
    from psycopg2 import sql

    conn = config.open_postgres(url)
    try:
        conn.set_session(readonly=True, autocommit=True)   # read-only belt + suspenders
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        print("=" * 72)
        print("Import dry-run: categories (staged Vision vs. velocity_current)  [read-only]")
        print("=" * 72)

        # Source rows: each staged category fans out to 0/1/2 rows -> flatten.
        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblPartsCategories")))
        raw = cur.fetchall()
        source_rows: list[dict[str, Any]] = [
            c for cr in raw for c in transform_category(dict(cr))   # both-case -> two rows
        ]

        # No stored legacy keys exist on a first run (the legacy_* columns only land
        # when this migration is applied to prod), so nothing UPDATEs yet.
        existing_by_legacy: dict[tuple[str, int], int] = {}

        # existing_by_natural: {(name, type) -> live category id} from the live copy.
        existing_by_natural: dict[Hashable, int] = {}
        cur.execute(sql.SQL("SELECT id, name, type FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("categories")))
        for r in cur.fetchall():
            key = (to_str(r["name"]), to_str(r["type"]))            # match the provisional key
            if key[0]:
                existing_by_natural.setdefault(key, r["id"])        # first-wins on dup name+type

        decisions = decide(source_rows, CATEGORY_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source categories (staged, fanned out):   {len(source_rows):>7}")
        print(f"  existing live categories (by name+type):  {len(existing_by_natural):>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing live row):    {counts[ADOPT]:>7}")
        print(f"    insert (new, not present in live):      {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):  {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier): {counts[DUP]:>7}")
        print(f"    skip   (blank/absent CategoryID):       {counts[SKIP]:>7}")
        print("\n  NOTE (decision): natural key is PROVISIONAL (name, type). Also the")
        print("  'Application & Material' both-case yields two rows sharing one CategoryID,")
        print("  which would collide on the (legacy_source, legacy_id) unique index in a")
        print("  real write path -- keying for that case is an open decision.")
        print("=" * 72)
        return counts
    finally:
        conn.close()
