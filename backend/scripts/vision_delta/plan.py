"""Make the cutover plan on a LOCAL copy of production, and measure what each batch writes.

Run from ``backend/`` on a fresh local copy of production (it is changed: every
batch is applied to it, to measure)::

    python -m scripts.vision_delta.plan --csv <export folder> --target <local postgres url> --out <folder>
                                        [--decisions decisions.csv] [--active-since 2018-01-01]
                                        [--batch-rows 1500] [--june-cutoff YYYY-MM-DD]

What it does:

1. compares the export with the copy and decides every row (the rule, the
   holds, a decisions file), as apply.py does;
2. keeps only projects with Vision activity on or after ``--active-since``
   (Dexter's eight years); rows of older projects are left for the archive
   (they are listed, not written);
3. groups the rows by project into batches (a project is never split), the
   catalogue first, then projects keeping their number, then the rest, then
   projects that get a new number;
4. applies every batch to the copy through the same code the admin API runs
   (runner.py), recording how much write-ahead log and database growth each
   one caused: the estimate prod's pre-flight check uses;
5. writes ``plan.json`` (for push.py), ``report.xlsx`` and ``decisions_needed.csv``
   (as apply.py does), and the change log under ``<out>/log``.

Production is never written by this script; push.py sends the plan to the API.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Optional
from urllib.parse import urlparse

ARCHIVE = "left for the archive"                           # reason prefix of rows on inactive projects
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
LEFT_PART = 5000                                           # left rows per request


def _day(v) -> Optional[date]:
    """A date or datetime as a date (None stays None)."""
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def last_activity(v: dict, project_map: dict[str, str]) -> dict[str, date]:
    """Newest Vision date per project label (the project's start, its work orders and lines, its POs).

    Args:
        v: The Vision data (quotes and POs relabelled by the compare).
        project_map: Vision number -> label rows carry (see compare.compare_projects).

    Returns:
        label -> newest date. A label with no dated Vision record is absent.
    """
    out: dict[str, date] = {}

    def bump(uca, *days):
        label = project_map.get(uca, uca)                  # the label rows of this project carry
        for d in map(_day, days):
            if d and (label not in out or d > out[label]):
                out[label] = d

    for p in v["projects"]:
        bump(p["uca"], p.get("created_on"))
    for q in v["quotes"]:
        bump(q.get("vision_uca", q["uca"]), q.get("created_at"), q.get("entered"), q.get("revised"),
             *(l.get("entered") for l in q.get("lines", [])))
    for po in v["pos"]:
        bump(po.get("vision_uca", po["uca"]), po.get("created_at"))
    return out


def _item_vids(rows: list[dict]) -> set:
    """Vision inventory ids the rows' lines point at (to ship only the item links a batch uses)."""
    from .compare import MISSING
    out = set()
    for r in rows:
        rec = (r.get("_ref") or {}).get("vision") or {}
        if r["level"] in ("quotes", "pos") and r["kind"] == MISSING:
            out |= {l.get("item_vid") for l in rec.get("lines", [])}
        elif r["level"] in ("quote lines", "po lines"):
            out.add(rec.get("item_vid"))
    out.discard(None)
    return out


def _order(label: str, rows: list[dict]) -> tuple:
    """Batch order of a project: catalogue, kept-number inserts, the rest, renumbered inserts."""
    from .compare import MISSING
    if not label:
        return (0, "")                                     # customers, vendors, inventory: first
    inserts = [r for r in rows if r["level"] == "projects" and r["kind"] == MISSING]
    if any(not r["_ref"].get("new_number") for r in inserts):
        return (1, label)                                  # keeps its number: before any new number is handed out
    if inserts:
        return (3, label)                                  # gets the next free number: last
    return (2, label)


def build(v: dict, delta, decisions: dict, holds: Optional[dict], active_since: date,
          batch_rows: int = 1500) -> tuple[list[dict], list[dict], dict]:
    """Decide every row and group what is to be written into batches.

    Args:
        v: The Vision data the compare read.
        delta: The compare's results.
        decisions: apply.load_decisions output.
        holds: apply.find_holds output.
        active_since: Projects whose newest Vision activity is older are left for the archive.
        batch_rows: Rows (and paired records) per batch, roughly; a project is never split.

    Returns:
        ``(batches, left, stats)``: the batches (not yet encoded or hashed),
        the report rows left as they are (archived projects included) and counts.
    """
    from models import Project, PurchaseOrder, Quote
    from .apply import SKIPPED, decide, report_row, stamp_plan
    from .compare import DIFFERS
    to_apply, left = decide(delta, decisions, holds)
    active = last_activity(v, delta.project_map)
    keep = lambda label: not label or label not in active or active[label] >= active_since   # unknown: kept
    stats = Counter()

    # Rows per project label; rows of inactive projects are left for the archive
    unit_rows: dict[str, list] = defaultdict(list)
    for r in to_apply:
        label = r["project"] or ""
        if keep(label):
            unit_rows[label].append(r)
        else:
            left.append(report_row(r, SKIPPED, f"{ARCHIVE}: last vision activity {active[label]}"))
            stats["archived rows"] += 1

    # Paired records a batch needs: the quotes/POs its rows change, and those with a Vision number to record
    stamp_ids = {model: {row["id"] for row in batch} for model, batch in stamp_plan(delta)[0]}
    refs = {"quote_pairs": set(), "po_pairs": set(), "project_pairs": set()}
    for r in to_apply:
        if r["level"] == "quotes" and r["kind"] == DIFFERS:
            refs["quote_pairs"].add(r["velocity_key"])
        elif r["level"] == "quote lines":
            refs["quote_pairs"].add(r["_ref"]["parent"])
        elif r["level"] == "pos" and r["kind"] == DIFFERS:
            refs["po_pairs"].add(r["velocity_key"])
        elif r["level"] == "po lines":
            refs["po_pairs"].add(r["_ref"]["parent"])
    unit_pairs: dict[str, dict] = defaultdict(lambda: {"quote_pairs": [], "po_pairs": [], "project_pairs": []})
    for name, pairs, model in (("quote_pairs", delta.quote_pairs, Quote), ("po_pairs", delta.po_pairs, PurchaseOrder),
                               ("project_pairs", delta.project_pairs, Project)):
        for vrec, trec in pairs:
            if trec["id"] not in refs[name] and trec["id"] not in stamp_ids.get(model, ()):
                continue                                   # nothing to do on it
            if keep(trec["uca"]):
                unit_pairs[trec["uca"]][name].append([vrec, trec])
            else:
                stats["archived stamps"] += 1

    # Pack projects into batches, in order; the catalogue is a batch of its own
    labels = sorted(set(unit_rows) | set(unit_pairs), key=lambda lb: _order(lb, unit_rows.get(lb, [])))
    groups, cur, size = [], [], 0
    for lb in labels:
        n = len(unit_rows.get(lb, [])) + (sum(len(p) for p in unit_pairs[lb].values()) if lb in unit_pairs else 0)
        if cur and (size + n > batch_rows or cur == [""]):
            groups.append(cur)
            cur, size = [], 0
        cur.append(lb)
        size += n
    if cur:
        groups.append(cur)

    batches = []
    for i, group in enumerate(groups, 1):
        rows = [r for lb in group for r in unit_rows.get(lb, [])]
        vids = _item_vids(rows)
        in_group = set(group)
        batches.append({
            "key": f"{i:04d}", "projects": group, "rows": rows,
            "links": [[k, vid, tid] for (k, vid), tid in delta.links.items() if vid in vids],
            "project_map": {vu: lb for vu, lb in delta.project_map.items() if lb in in_group},
            **{name: [p for lb in group if lb in unit_pairs for p in unit_pairs[lb][name]]
               for name in ("quote_pairs", "po_pairs", "project_pairs")},
        })
    stats.update(batches=len(batches), rows=len(to_apply) - stats["archived rows"], left=len(left))
    return batches, left, dict(stats)


def plan_hash(batches: list[dict]) -> str:
    """SHA-256 of the batches' content (estimates and the hash itself left out)."""
    from .runner import encode
    body = [{k: b[k] for k in b if k not in ("estimate", "plan")} for b in batches]
    return hashlib.sha256(json.dumps(encode(body), sort_keys=True).encode("utf-8")).hexdigest()


def measure(meta: dict, batches: list[dict], left: list[dict], url: str, store) -> tuple[int, list[dict]]:
    """Apply the plan to the local copy through runner.py, measuring each batch.

    Each batch goes through the JSON round trip the API's requests take, so
    the local run applies exactly what production will receive. Its
    ``estimate`` (WAL bytes, database growth, seconds) is set in place.

    Args:
        meta: The plan's meta (its ``hash`` set).
        batches: The plan's batches (``plan`` set on each).
        left: The rows left as they are.
        url: The LOCAL copy's URL.
        store: Where the change log goes.

    Returns:
        ``(run id, report)``: the local run and every report row (left + applied).
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from . import runner
    engine = create_engine(url)
    report = list(left)
    try:
        with Session(engine) as db:
            run_id, _ = runner.start_run(db, meta, getpass.getuser())
            for part, i in enumerate(range(0, len(left), LEFT_PART), 1):
                body = runner.decode(json.dumps(runner.encode(left[i:i + LEFT_PART])))
                runner.add_left(db, run_id, part, body, store)
            for n, b in enumerate(batches, 1):
                lsn0, size0 = db.execute(text("SELECT pg_current_wal_lsn(), pg_database_size(current_database())")).one()
                db.rollback()
                started = time.time()
                wire = runner.decode(json.dumps(runner.encode(b)))   # what the API decodes
                result, _, rows = runner.run_batch(db, run_id, wire, store)
                wal, size1 = db.execute(text("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), CAST(:l AS pg_lsn)), "
                                             "pg_database_size(current_database())"), {"l": str(lsn0)}).one()
                db.rollback()
                b["estimate"] = {"wal_bytes": int(wal), "db_bytes": int(size1 - size0), "rows": len(b["rows"]),
                                 "seconds": round(time.time() - started, 1)}
                report += rows
                print(f"  batch {b['key']} ({n}/{len(batches)}): {len(b['rows'])} rows, "
                      f"WAL {wal / runner.MB:.1f} MB, growth {(size1 - size0) / runner.MB:.1f} MB, "
                      f"{b['estimate']['seconds']}s, {result['actions']}")
            runner.finish_run(db, run_id)
    finally:
        engine.dispose()
    return run_id, report


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, compare, build, measure on the local copy, write the plan and the report.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv``).

    Returns:
        Process exit code (1 when any record failed on the local copy).
    """
    ap = argparse.ArgumentParser(prog="python -m scripts.vision_delta.plan", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="folder holding the Vision CSV export (tblX.csv)")
    ap.add_argument("--target", default=os.getenv("VISION_DELTA_TARGET_URL"),
                    help="a LOCAL, fresh copy of production (it is changed)")
    ap.add_argument("--out", required=True, help="output folder (plan.json, report.xlsx, log/)")
    ap.add_argument("--decisions", help="decisions_needed.csv with its decision column filled")
    ap.add_argument("--active-since", type=date.fromisoformat, default=date(2018, 1, 1),
                    help="keep projects with vision activity on or after this day (default 2018-01-01)")
    ap.add_argument("--batch-rows", type=int, default=1500, help="rows per batch, roughly (default 1500)")
    ap.add_argument("--june-cutoff", type=date.fromisoformat, help="day of the June import (default: estimated)")
    ap.add_argument("--projects-since", type=date.fromisoformat, default=date(2025, 1, 1),
                    help="first creation day for the per-project sheet (default: 2025-01-01)")
    args = ap.parse_args(argv)
    if not args.target:
        ap.error("--target (or VISION_DELTA_TARGET_URL) is required")
    host = urlparse(args.target).hostname
    if host not in LOCAL_HOSTS:
        ap.error(f"--target must be a local copy (got host {host}); production is applied through push.py")

    os.environ.setdefault("DATABASE_URL", args.target)    # app modules read it at import
    from log_store import FolderStore
    from . import compare, velocity, vision
    from .apply import check_schema, find_holds, fingerprint, load_decisions, write_report
    from .runner import encode

    started = time.time()
    v = vision.load(args.csv)
    conn = velocity.connect(args.target)
    try:
        t = velocity.load(conn)
    finally:
        conn.close()
    cutoff = args.june_cutoff or t["recorded_cutoff"] or compare.june_cutoff(t["quotes"], t["first_snapshot"])
    if cutoff is None:
        print("no imported quotes found; pass --june-cutoff", file=sys.stderr)
        return 2
    delta = compare.run(v, t, cutoff)
    print(f"compared: {len(delta.rows)} difference rows (june import {cutoff})")
    batches, left, stats = build(v, delta, load_decisions(args.decisions), find_holds(v, delta),
                                 args.active_since, args.batch_rows)
    digest = plan_hash(batches)
    for b in batches:
        b["plan"] = digest
    meta = {"hash": digest, "export_fingerprint": fingerprint(args.csv), "vision_export": os.path.abspath(args.csv),
            "june_cutoff": str(cutoff), "active_since": str(args.active_since), "batches": len(batches),
            "made_at": datetime.now().replace(microsecond=0).isoformat(), "made_on": host, **stats}
    print(f"plan {digest[:12]}: {stats}")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine(args.target)
    try:
        with Session(engine) as db:
            check_schema(db)
    finally:
        engine.dispose()
    store = FolderStore(os.path.join(args.out, "log"))
    run_id, report = measure(meta, batches, left, args.target, store)

    wal = sum(b["estimate"]["wal_bytes"] for b in batches)
    grow = sum(b["estimate"]["db_bytes"] for b in batches)
    meta.update(local_run=run_id, wal_mb=round(wal / 1048576, 1), growth_mb=round(grow / 1048576, 1),
                largest_batch_wal_mb=round(max((b["estimate"]["wal_bytes"] for b in batches), default=0) / 1048576, 1))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "plan.json"), "w", encoding="utf-8") as f:
        json.dump(encode({"meta": meta, "batches": batches, "left": left}), f)
    counts = Counter(r["outcome"] for r in report)
    paths = write_report(report, delta, v, t, {**meta, **{f"outcome {k}": n for k, n in sorted(counts.items())}},
                         args.out, args.projects_since)
    print(f"\nplan -> {os.path.join(args.out, 'plan.json')}\nreport -> {paths[0]}\n"
          f"local run {run_id}: WAL {meta['wal_mb']} MB in all, largest batch {meta['largest_batch_wal_mb']} MB, "
          f"database growth {meta['growth_mb']} MB ({time.time() - started:.0f}s)")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
