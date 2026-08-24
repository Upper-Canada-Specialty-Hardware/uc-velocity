"""Read-only dry run of the import engine for the project domain.

Reads staged Vision ``tblProjects`` and the read-only ``velocity_current`` copy of
live Velocity, asks the engine what the importer WOULD do for each project
(adopt / insert / update / skip), and prints the tally -- writing nothing.

The natural key is the **UCA project number**: the original importer set each
Velocity project's ``uca_project_number`` from Vision's UCA number, and that column
is UNIQUE in Velocity -- so it lines a staged project up against its existing live
row cleanly (unlike customers/vendors, whose only key is the name). Cross-domain
links (a project's customer via ``client_legacy_id``, its lead via ``project_lead``)
are NOT resolved here -- that belongs to the write path.
"""
from __future__ import annotations

from .. import config
from ..transform import transform_project, to_str
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy" (the staged Vision copy)
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current" (the live-Velocity copy)

# Route a staged project through the engine: its Vision primary key is ProjectID
# (stored as legacy_id); its natural key is the UCA number the importer already
# wrote onto the live project (uca_project_number, unique in Velocity).
PROJECT_SPEC = DomainSpec(
    legacy_source="tblProjects",
    legacy_id_of=lambda r: r["project_legacy_id"] or None,      # 0/missing -> None -> skip
    natural_key_of=lambda r: r["uca_project_number"] or None,   # unique -> safe to adopt by
)


def run_project_dry_run(url: str) -> dict[str, int]:
    """Print the project-domain dry-run ledger and return the action counts.

    Args:
        url: Staging Postgres URL (holds ``vision_legacy`` and, after
            ``load-velocity`` has run, ``velocity_current``).

    Returns:
        ``{action: count}`` from :func:`engine.summarize`.
    """
    import psycopg2.extras                                   # imported lazily like the other dry-runs
    from psycopg2 import sql

    conn = config.open_postgres(url)
    try:
        conn.set_session(readonly=True, autocommit=True)     # read-only session, belt + suspenders
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        print("=" * 72)
        print("Import dry-run: projects (staged Vision vs. velocity_current)  [read-only]")
        print("=" * 72)

        # Source rows: every staged Vision project, transformed to a project dict.
        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblProjects")))
        source_rows = [transform_project(dict(r)) for r in cur.fetchall()]

        # No stored legacy keys on a first run (the legacy_* columns are empty on live
        # rows until this migration is applied to prod) -> nothing UPDATEs by key.
        existing_by_legacy: dict[tuple[str, int], int] = {}

        # existing_by_natural: {uca_project_number -> live project id}. Unique column,
        # so no collision handling is needed (unlike name-keyed profiles).
        existing_by_natural: dict[object, int] = {}
        cur.execute(sql.SQL("SELECT id, uca_project_number FROM {}.{}").format(
            sql.Identifier(VC), sql.Identifier("projects")))
        for r in cur.fetchall():
            uca = to_str(r["uca_project_number"])            # normalise to a comparable string
            if uca:
                existing_by_natural.setdefault(uca, r["id"])

        decisions = decide(source_rows, PROJECT_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source projects (staged Vision):          {len(source_rows):>7}")
        print(f"  existing live projects (by UCA number):   {len(existing_by_natural):>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing live project):{counts[ADOPT]:>7}")
        print(f"    insert (new, not present in live):      {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):  {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier): {counts[DUP]:>7}")
        print(f"    skip   (project has no id):             {counts[SKIP]:>7}")
        print("=" * 72)
        return counts
    finally:
        conn.close()
