"""Archive the projects with no activity since 2018: build the files, then remove the rows.

Dexter's rule (#304): a project stays in the database when its newest Vision
activity (start, work orders and their lines, POs) is on or after 2018-01-01.
Every other project is archived, as long as nobody has touched it in Velocity
since the June import: no quote or PO made after it, no invoice, no receiving,
no snapshot by a person. A touched project stays, whatever its age.

The archive is one JSON file per project in the bucket, under
``vision-archive/``:

* ``projects/<uca>.json`` -- Vision's version (the project, its work orders
  with their lines, its POs with their lines) and Velocity's own rows, column
  for column, as ``row_to_json`` gives them (so they can be put back);
* ``index.json`` -- one entry per project for the archived-projects page;
* ``manifest.json`` -- each file's SHA-256 and the Velocity row ids it holds.

Building (this module's ``main``) reads the Vision export and the target
database and writes the files to a local folder; it never writes to the
database. ``push.py --archive`` sends the folder to the backend, which writes
it to the bucket and reads every file back. Removal (:func:`remove_project`,
run by the admin API in batches) deletes a project's rows only when its
archived Velocity rows still equal the database's, row for row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from typing import Optional

PREFIX = "vision-archive"                                  # key prefix in the bucket
FORMAT = 1                                                 # file layout version
TOOL_ACTOR = "vision-delta-tool"                           # the cutover's own snapshots are not a person's edit

# Velocity's rows of one project, children before parents is the delete order (reversed for restore)
CHILD_TABLES = [                                           # (table, parent table, SQL selecting the rows)
    ("quote_line_item_snapshots", "quote_snapshots",
     "SELECT t.* FROM quote_line_item_snapshots t JOIN quote_snapshots s ON s.id = t.snapshot_id "
     "JOIN quotes q ON q.id = s.quote_id WHERE q.project_id = :pid"),
    ("quote_snapshots", "quotes",
     "SELECT t.* FROM quote_snapshots t JOIN quotes q ON q.id = t.quote_id WHERE q.project_id = :pid"),
    ("quote_line_items", "quotes",
     "SELECT t.* FROM quote_line_items t JOIN quotes q ON q.id = t.quote_id WHERE q.project_id = :pid"),
    ("po_receiving_line_items", "po_receivings",
     "SELECT t.* FROM po_receiving_line_items t JOIN po_receivings r ON r.id = t.receiving_id "
     "JOIN purchase_orders o ON o.id = r.purchase_order_id WHERE o.project_id = :pid"),
    ("po_receivings", "purchase_orders",
     "SELECT t.* FROM po_receivings t JOIN purchase_orders o ON o.id = t.purchase_order_id WHERE o.project_id = :pid"),
    ("po_line_item_snapshots", "po_snapshots",
     "SELECT t.* FROM po_line_item_snapshots t JOIN po_snapshots s ON s.id = t.snapshot_id "
     "JOIN purchase_orders o ON o.id = s.purchase_order_id WHERE o.project_id = :pid"),
    ("po_snapshots", "purchase_orders",
     "SELECT t.* FROM po_snapshots t JOIN purchase_orders o ON o.id = t.purchase_order_id WHERE o.project_id = :pid"),
    ("po_line_items", "purchase_orders",
     "SELECT t.* FROM po_line_items t JOIN purchase_orders o ON o.id = t.purchase_order_id WHERE o.project_id = :pid"),
    ("quotes", "projects", "SELECT t.* FROM quotes t WHERE t.project_id = :pid"),
    ("purchase_orders", "projects", "SELECT t.* FROM purchase_orders t WHERE t.project_id = :pid"),
    ("projects", None, "SELECT t.* FROM projects t WHERE t.id = :pid"),
]


def sha(obj) -> str:
    """SHA-256 of an object's canonical JSON (sorted keys), as the manifest records it."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _day(v) -> Optional[date]:
    """A date or datetime as a date."""
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


# ---- which projects ------------------------------------------------------ #

def touched_projects(db, project_ids: list[int], june_day: date) -> dict[int, list[str]]:
    """Projects someone worked on in Velocity since the June import, and how.

    Args:
        db: A SQLAlchemy session.
        project_ids: Velocity projects to check.
        june_day: The first day after the June import; anything made on or after it is Velocity's.

    Returns:
        project id -> reasons, only for touched projects.
    """
    from sqlalchemy import text
    checks = {
        "a quote made in velocity": "SELECT project_id FROM quotes WHERE project_id = ANY(:ids) AND created_at >= :day",
        "a po made in velocity": "SELECT project_id FROM purchase_orders WHERE project_id = ANY(:ids) AND created_at >= :day",
        "an invoice": "SELECT q.project_id FROM invoices i JOIN quotes q ON q.id = i.quote_id WHERE q.project_id = ANY(:ids)",
        "a receiving": "SELECT o.project_id FROM po_receivings r JOIN purchase_orders o ON o.id = r.purchase_order_id "
                       "WHERE o.project_id = ANY(:ids)",
        "a quote edit by a person": "SELECT q.project_id FROM quote_snapshots s JOIN quotes q ON q.id = s.quote_id "
                                    "WHERE q.project_id = ANY(:ids) AND s.actor_user_id IS NOT NULL AND s.actor_user_id <> :tool",
        "a po edit by a person": "SELECT o.project_id FROM po_snapshots s JOIN purchase_orders o ON o.id = s.purchase_order_id "
                                 "WHERE o.project_id = ANY(:ids) AND s.actor_user_id IS NOT NULL AND s.actor_user_id <> :tool",
        "made in velocity": "SELECT id FROM projects WHERE id = ANY(:ids) AND created_on >= :day",
        # The cutover left a decision on it (decisions page): settle it first, never archive it unseen
        "a vision decision left by the cutover": """
            SELECT q.project_id FROM vision_sync_changes c JOIN quote_line_items l ON l.id = c.record_id
              JOIN quotes q ON q.id = l.quote_id
             WHERE c.record_table = 'quote_line_items' AND c.action IN ('held', 'kept: conflict') AND q.project_id = ANY(:ids)
            UNION SELECT q.project_id FROM vision_sync_changes c JOIN quotes q ON q.id = c.record_id
             WHERE c.record_table = 'quotes' AND c.action IN ('held', 'kept: conflict') AND q.project_id = ANY(:ids)
            UNION SELECT o.project_id FROM vision_sync_changes c JOIN po_line_items l ON l.id = c.record_id
              JOIN purchase_orders o ON o.id = l.purchase_order_id
             WHERE c.record_table = 'po_line_items' AND c.action IN ('held', 'kept: conflict') AND o.project_id = ANY(:ids)
            UNION SELECT o.project_id FROM vision_sync_changes c JOIN purchase_orders o ON o.id = c.record_id
             WHERE c.record_table = 'purchase_orders' AND c.action IN ('held', 'kept: conflict') AND o.project_id = ANY(:ids)
            UNION SELECT c.record_id FROM vision_sync_changes c
             WHERE c.record_table = 'projects' AND c.action IN ('held', 'kept: conflict') AND c.record_id = ANY(:ids)""",
    }
    out: dict[int, list[str]] = defaultdict(list)
    for why, sql in checks.items():
        for (pid,) in db.execute(text(sql), {"ids": project_ids, "day": june_day, "tool": TOOL_ACTOR}):
            if why not in out[pid]:
                out[pid].append(why)
    return dict(out)


def select(v: dict, db, active_since: date, june_day: date) -> tuple[list[dict], dict[str, list[str]]]:
    """The projects to archive.

    Args:
        v: The Vision export (:func:`scripts.vision_delta.vision.load`).
        db: A session on the target database (read only).
        active_since: Projects with Vision activity on or after this day stay.
        june_day: See :func:`touched_projects`.

    Returns:
        ``(chosen, kept)``: chosen = ``{"uca", "velocity_id" or None}`` per
        project; kept = Vision number -> why an old project stays.
    """
    from sqlalchemy import text
    from .plan import last_activity
    act = last_activity(v, {})                             # newest Vision date per Vision number
    old = sorted(u for u, d in act.items() if d < active_since)
    velocity = {}
    for pid, num in db.execute(text("SELECT id, coalesce(vision_project_number, uca_project_number) FROM projects")):
        velocity.setdefault(num, []).append(pid)
    ids = [pid for u in old for pid in velocity.get(u, [])]
    touched = touched_projects(db, ids, june_day)
    chosen, kept = [], {}
    for u in old:
        pids = velocity.get(u, [])
        if len(pids) > 1:
            kept[u] = ["more than one velocity project carries this number"]   # never guess which
        elif pids and pids[0] in touched:
            kept[u] = touched[pids[0]]
        else:
            chosen.append({"uca": u, "velocity_id": pids[0] if pids else None, "last_activity": act[u]})
    return chosen, kept


# ---- the files ----------------------------------------------------------- #

def velocity_rows(db, project_id: int) -> dict[str, list[dict]]:
    """Every Velocity row of one project, table by table, as ``row_to_json`` gives it.

    Rows are sorted by id so the same database gives the same file.
    """
    from sqlalchemy import text
    out = {}
    for table, _, sql in CHILD_TABLES:
        rows = [r[0] for r in db.execute(text(f"SELECT row_to_json(x) FROM ({sql}) x"), {"pid": project_id})]
        out[table] = sorted(rows, key=lambda r: r["id"])
    return out


def vision_version(v: dict, uca: str) -> dict:
    """Vision's version of one project: the project, its work orders and POs with their lines."""
    by = lambda recs: [r for r in recs if r.get("vision_uca", r["uca"]) == uca]   # noqa: E731
    project = next((p for p in v["projects"] if p["uca"] == uca), None)
    return {"project": project, "work_orders": by(v["quotes"]), "pos": by(v["pos"])}


def build_file_parts(uca: str, last_activity, velocity_id: Optional[int], vis: dict, vel: Optional[dict]) -> dict:
    """Assemble one project's archive file from parts already in hand (no database access).

    The admin build route (#304) uses this: it has Vision's version from the
    request and reads Velocity's rows itself, so it never needs the Vision export.

    Args:
        uca: The UCA project number.
        last_activity: Newest Vision activity (a date, or its string form in a stored file).
        velocity_id: The Velocity project id, or None for a Vision-only project.
        vis: Vision's version (see :func:`vision_version`): project, work orders, POs.
        vel: Velocity's rows (see :func:`velocity_rows`), or None when not in Velocity.

    Returns:
        The archive file dict (not yet JSON-normalised: the caller stores it).
    """
    p = vis.get("project") or {}                           # the Vision project row, or none for a Velocity-only match
    return {"format": FORMAT, "uca": uca, "name": p.get("name"), "customer": p.get("customer"),
            "created_on": p.get("created_on"), "last_activity": last_activity,
            "velocity_project_id": velocity_id, "vision": vis, "velocity": vel}


def build_file(v: dict, db, pick: dict) -> dict:
    """One project's archive file (local build path: reads the export and the database)."""
    vis = vision_version(v, pick["uca"])                   # Vision's version from the export
    vel = velocity_rows(db, pick["velocity_id"]) if pick["velocity_id"] else None   # Velocity's rows, if any
    return build_file_parts(pick["uca"], pick["last_activity"], pick["velocity_id"], vis, vel)


def index_entry(f: dict, key: str) -> dict:
    """The archived-projects page's row for one file."""
    vel = f["velocity"] or {}
    return {"uca": f["uca"], "name": f["name"], "customer": f["customer"], "created_on": f["created_on"],
            "last_activity": f["last_activity"], "work_orders": len(f["vision"]["work_orders"]),
            "pos": len(f["vision"]["pos"]), "velocity_quotes": len(vel.get("quotes", [])),
            "velocity_pos": len(vel.get("purchase_orders", [])), "in_velocity": f["velocity"] is not None,
            "key": key}


def write_folder(files: list[dict], out_dir: str, meta: dict) -> dict:
    """Write the files, the index and the manifest to a local folder.

    Returns:
        The manifest.
    """
    from log_store import FolderStore
    store = FolderStore(out_dir)
    index, manifest = [], {"meta": meta, "files": {}}
    for f in files:
        key = f"{PREFIX}/projects/{f['uca']}.json"
        doc = json.loads(json.dumps(f, default=str))       # exactly what the file will hold
        store.put_json(key, doc)
        index.append(index_entry(doc, key))
        manifest["files"][key] = {"sha256": sha(doc), "velocity_project_id": f["velocity_project_id"],
                                  "rows": {t: [r["id"] for r in rs] for t, rs in (f["velocity"] or {}).items()}}
    index.sort(key=lambda r: r["uca"])
    store.put_json(f"{PREFIX}/index.json", {"meta": meta, "projects": index})
    store.put_json(f"{PREFIX}/manifest.json", manifest)
    return manifest


# ---- removal (run by the admin API, one project at a time) -------------- #

class NotSafe(Exception):
    """A project that must not be removed; nothing was deleted."""


def remove_project(db, archived: dict, june_day: date) -> dict[str, int]:
    """Delete one archived project's Velocity rows, after checking they are exactly what was archived.

    The caller holds the transaction and commits.

    Args:
        db: A SQLAlchemy session on the database.
        archived: The project's archive file (read back from the bucket).
        june_day: See :func:`touched_projects`.

    Returns:
        table -> rows deleted.

    Raises:
        NotSafe: The project changed since it was archived, or someone touched it.
    """
    from sqlalchemy import text
    pid = archived.get("velocity_project_id")
    if not pid:
        return {}                                          # Vision-only: nothing in the database
    db.execute(text("SELECT id FROM projects WHERE id = :pid FOR UPDATE"), {"pid": pid})   # hold it while checking
    touched = touched_projects(db, [pid], june_day)
    if touched:
        raise NotSafe(f"{archived['uca']}: {', '.join(touched[pid])} since the archive")
    now = velocity_rows(db, pid)
    for table, rows in (archived.get("velocity") or {}).items():
        if sha(now.get(table, [])) != sha(rows):
            raise NotSafe(f"{archived['uca']}: {table} differs from the archived copy")
    out = {}
    for table, _, sql in CHILD_TABLES:                     # children first
        ids = [r["id"] for r in now[table]]
        if ids:
            db.execute(text(f"DELETE FROM {table} WHERE id = ANY(:ids)"), {"ids": ids})
        out[table] = len(ids)
    return out


# ---- command line -------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    """Build the archive files into a local folder (reads the export and the target; writes no database)."""
    p = argparse.ArgumentParser(description="build the archive of projects with no activity since 2018")
    p.add_argument("--csv", required=True, help="the Vision export folder")
    p.add_argument("--target", required=True, help="Postgres URL to read (production, or a copy)")
    p.add_argument("--out", required=True, help="local folder for the files")
    p.add_argument("--active-since", default="2018-01-01", help="projects with Vision activity on or after this stay")
    p.add_argument("--june-day", default="2026-06-19", help="first day after the June import")
    a = p.parse_args(argv)
    if os.path.exists(os.path.join(a.out, PREFIX, "manifest.json")):
        print(f"{a.out} already holds an archive; use a new folder")
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from . import vision
    since, june = date.fromisoformat(a.active_since), date.fromisoformat(a.june_day)
    v = vision.load(a.csv)
    with Session(create_engine(a.target)) as db:
        db.execute(__import__("sqlalchemy").text("SET TRANSACTION READ ONLY"))   # this step never writes
        chosen, kept = select(v, db, since, june)
        files = [build_file(v, db, pick) for pick in chosen]
        db.rollback()
    meta = {"made_at": datetime.utcnow().isoformat(sep=" "), "active_since": a.active_since,
            "june_day": a.june_day, "projects": len(files),
            "in_velocity": sum(1 for f in files if f["velocity_project_id"])}
    manifest = write_folder(files, a.out, meta)
    rows = defaultdict(int)
    for m in manifest["files"].values():
        for t, ids in m["rows"].items():
            rows[t] += len(ids)
    print(f"archived {len(files)} projects ({meta['in_velocity']} in velocity) into {a.out}")
    print("velocity rows held:", dict(rows))
    print(f"old projects kept because they were touched: {len(kept)}")
    for u, why in sorted(kept.items())[:20]:
        print(f"  {u}: {', '.join(why)}")
    with open(os.path.join(a.out, "kept.json"), "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
