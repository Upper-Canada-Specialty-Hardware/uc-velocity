"""Read-only dry run of the import engine for the staff domain.

Reads the staged Vision ``tblEmployees`` table, transforms each row into a 'staff'
Profile (see :func:`transform.extract_staff`), and asks the engine what the importer
WOULD do for each -- adopt / insert / update / skip -- against the read-only
``velocity_current`` copy of live Velocity. It prints the tally and a sample of the
insert shape (including the contact), and writes NOTHING.

Keying: a staff member's identity is its real ``EmployeeID`` (legacy_source
``tblEmployees``), so same-name people stay distinct and a re-run UPDATEs by key
instead of duplicating. Staff are NOT adopted by name -- a hand-created staff Profile
in Velocity has no EmployeeID to match, and matching on name would wrongly merge the
two distinct "Cory DaSilva" people -- so on a first run every employee INSERTs.
"""
from __future__ import annotations

from typing import Any

from .. import config
from ..transform import extract_staff, STAFF_LEGACY_SOURCE
from .engine import DomainSpec, decide, summarize, ADOPT, INSERT, UPDATE, DUP, SKIP

SCHEMA = config.STAGING_SCHEMA            # "vision_legacy" (the staged Vision copy)
VC = config.VELOCITY_CURRENT_SCHEMA       # "velocity_current" (the live-Velocity copy)

# How to route a staff row through the engine. Its Vision primary key AND its
# identity are both EmployeeID: the id we would store, and the value that keeps two
# same-name people distinct. natural_key == legacy_id means "only adopt something
# that already carries this EmployeeID" -- which no un-keyed live row does -- so
# adoption never fires for staff and un-imported employees INSERT.
STAFF_SPEC = DomainSpec(
    legacy_source=STAFF_LEGACY_SOURCE,
    legacy_id_of=lambda r: r["staff_legacy_id"] or None,   # EmployeeID; 0/None -> skip
    natural_key_of=lambda r: r["staff_legacy_id"] or None, # identity is the EmployeeID, never the name
)


def run_staff_dry_run(url: str) -> dict[str, int]:
    """Print the staff-domain dry-run ledger and return the action counts.

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
        print("Import dry-run: staff (staged Vision tblEmployees vs. velocity_current)  [read-only]")
        print("=" * 72)

        # Source rows: every staged employee, transformed to a staff Profile dict.
        cur.execute(sql.SQL("SELECT * FROM {}.{}").format(
            sql.Identifier(SCHEMA), sql.Identifier("tblEmployees")))
        source_rows = extract_staff(dict(r) for r in cur.fetchall())

        # existing_by_legacy: the live copy carries no legacy key yet (the
        # legacy_source/legacy_id columns land on prod only when this migration is
        # applied), so nothing UPDATEs on a first run -- empty by construction. Once a
        # real import has stamped keys, a later run would UPDATE these in place.
        existing_by_legacy: dict[tuple[str, int], int] = {}

        # existing_by_natural: intentionally empty. Staff identity is EmployeeID, which
        # a hand-created live staff Profile does not carry, so there is nothing to
        # adopt by natural key -- un-imported employees INSERT.
        existing_by_natural: dict[Any, int] = {}

        # For context only (read-only): how many staff profiles already exist live.
        cur.execute(sql.SQL("SELECT count(*) AS n FROM {}.{} WHERE type = %s").format(
            sql.Identifier(VC), sql.Identifier("profiles")), ("staff",))
        existing_staff_live = cur.fetchone()["n"]

        # Plan (no writes) and tally.
        decisions = decide(source_rows, STAFF_SPEC, existing_by_legacy, existing_by_natural)
        counts = summarize(decisions)

        print(f"\n  source employees (staged tblEmployees):    {len(source_rows):>7}")
        print(f"  staff profiles already in live Velocity:   {existing_staff_live:>7}")
        print("\n  the importer would:")
        print(f"    adopt  (claim an existing staff profile):{counts[ADOPT]:>7}")
        print(f"    insert (new staff, not present in live): {counts[INSERT]:>7}")
        print(f"    update (already keyed by a prior run):   {counts[UPDATE]:>7}")
        print(f"    dup    (same natural key seen earlier):  {counts[DUP]:>7}")
        print(f"    skip   (row has no EmployeeID):          {counts[SKIP]:>7}")

        # Show ONE transformed record so the reviewer can eyeball the field + contact
        # mapping the writer will use (name, nullable address/postal, one contact with
        # its phone_numbers). Purely illustrative -- still writes nothing.
        if source_rows:
            s = source_rows[0]
            c = s["contacts"][0] if s["contacts"] else {}
            print("\n  sample insert shape (first employee):")
            print(f"    name={s['name']!r}  address={s['address']!r}  postal={s['postal_code']!r}")
            print(f"    staff_roles={s['staff_roles']!r}")
            print(f"    contact: name={c.get('name')!r} email={c.get('email')!r} "
                  f"phone(work)={c.get('phone')!r} cell(mobile)={c.get('cell')!r}")
        print("=" * 72)
        return counts
    finally:
        conn.close()
