"""Apply a cutover plan one batch at a time: the part shared by the local rehearsal and the admin API.

A plan (see plan.py) is the compare's decided rows, grouped by project into
batches. Each batch is applied here in its own transaction:

* the run's row in ``vision_sync_runs`` is locked first, so two requests never
  apply batches at the same time;
* a batch already applied under the run is not applied again (the stored
  result is returned), so a lost response or a retry is safe;
* every field write still checks that Velocity holds the value the compare saw
  (see apply.py), so a record staff changed after the plan was made is skipped;
* the batch's change log goes to the log store (a storage bucket, see
  log_store.py) before the commit; when the commit fails the object is removed.

Only the rows a person must look at (conflicts and held rows) are written to
``vision_sync_changes``; everything else the run did or left is in the store.

The pre-flight check (:func:`preflight`, :func:`over_budget`) refuses a batch
when the write-ahead log since the last checkpoint, plus what the batch was
measured to write on the local copy, would pass a budget; or when the WAL
archiver is behind; or when the database and its log would pass the disk
limit. That is the control the 2026-10-29 crash lacked: one run wrote more WAL
inside one checkpoint cycle than the volume had room for.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

MB = 1024 * 1024
LOG_PREFIX = "vision-cutover"                              # object keys: vision-cutover/run-<id>/...
DB_ACTIONS = ("kept: conflict", "held")                    # the rows a person resolves: kept in Postgres
SAFETY = 1.5                                               # measured locally; prod may write more (full-page images)


# --------------------------------------------------------------------------- #
# JSON that keeps dates and money exact
# --------------------------------------------------------------------------- #

def encode(o):
    """A value as plain JSON, with dates, times and decimals tagged so :func:`decode` restores them.

    Args:
        o: Compare data (dicts, lists, tuples, sets, scalars).

    Returns:
        JSON-ready data.

    Raises:
        TypeError: A dict key that is not text, or a value JSON cannot hold.
    """
    if isinstance(o, datetime):                            # before date: a datetime is a date
        return {"$dt": o.isoformat()}
    if isinstance(o, date):
        return {"$d": o.isoformat()}
    if isinstance(o, Decimal):
        return {"$dec": str(o)}                            # exact, never through float
    if isinstance(o, dict):
        if any(not isinstance(k, str) for k in o):
            raise TypeError(f"non-text dict key in {list(o)[:3]}")
        return {k: encode(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [encode(v) for v in o]
    if o is None or isinstance(o, (str, int, float, bool)):
        return o
    value = getattr(o, "value", None)                      # an enum: its stored text
    if isinstance(value, (str, int)):
        return value
    raise TypeError(f"cannot encode {type(o).__name__}")


def _tagged(d: dict):
    """json object hook: a tagged value back to its type."""
    if len(d) == 1:
        (k, v), = d.items()
        if k == "$dt":
            return datetime.fromisoformat(v)
        if k == "$d":
            return date.fromisoformat(v)
        if k == "$dec":
            return Decimal(v)
    return d


def decode(text: str | bytes):
    """Parse JSON written from :func:`encode` output."""
    return json.loads(text, object_hook=_tagged)


# --------------------------------------------------------------------------- #
# A batch as the update tool's input
# --------------------------------------------------------------------------- #

def delta_of(batch: dict):
    """A :class:`compare.Delta` holding one batch, as :func:`apply.apply` reads it.

    Args:
        batch: A plan batch (decoded).

    Returns:
        The Delta: rows, the item links and project labels they use, and the
        paired records their quotes, POs and Vision numbers need.
    """
    from .compare import Delta
    d = Delta()
    d.rows = batch["rows"]
    d.links = {(k, vid): tid for k, vid, tid in batch["links"]}   # JSON has no tuple keys
    d.project_map = dict(batch["project_map"])
    d.quote_pairs = [tuple(p) for p in batch["quote_pairs"]]
    d.po_pairs = [tuple(p) for p in batch["po_pairs"]]
    d.project_pairs = [tuple(p) for p in batch["project_pairs"]]
    return d


def log_key(run_id: int, name: str) -> str:
    """Store key of one of a run's log objects."""
    return f"{LOG_PREFIX}/run-{run_id}/{name}.json"


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #

def _totals(run) -> dict:
    """A copy of the run's ``totals`` (assigned back whole, so the JSON column is saved)."""
    return json.loads(json.dumps(run.totals or {}))


def start_run(db, meta: dict, actor: str) -> tuple[int, bool]:
    """The run for a plan: an open one with the same plan hash, or a new one.

    Args:
        db: An open session (committed here).
        meta: The plan's ``meta`` (``hash``, ``export_fingerprint``,
            ``june_cutoff``, ``active_since``, ``vision_export``, ``batches``).
        actor: Who runs it (shown on the run).

    Returns:
        ``(run id, created)``.

    Raises:
        ValueError: The plan carries no hash, or a finished run applied it already.
    """
    from models import VisionSyncRun
    from .apply import table_counts
    if not meta.get("hash"):
        raise ValueError("the plan has no hash")
    for run in db.query(VisionSyncRun).filter(VisionSyncRun.mode == "commit"):
        if (run.totals or {}).get("plan") != meta["hash"]:
            continue
        if run.finished_at is not None:                    # a plan is applied once: make a new plan instead
            raise ValueError(f"run {run.id} applied this plan already (finished {run.finished_at:%Y-%m-%d %H:%M})")
        return run.id, False                               # the same plan again: carry on with its run
    run = VisionSyncRun(started_at=datetime.utcnow(), mode="commit", actor=actor,
                        vision_export=meta.get("vision_export"), export_fingerprint=meta.get("export_fingerprint"),
                        june_cutoff=str(meta.get("june_cutoff")), counts_before=table_counts(db),
                        totals={"plan": meta["hash"], "active_since": str(meta.get("active_since")),
                                "batches_planned": meta.get("batches"), "batches": {}, "left": {}})
    db.add(run)
    db.commit()
    return run.id, True


def lock_run(db, run_id: int):
    """The open run's row, locked for this transaction.

    Raises:
        LookupError: No such run.
        ValueError: The run is finished.
    """
    from models import VisionSyncRun
    run = db.query(VisionSyncRun).filter(VisionSyncRun.id == run_id).with_for_update().one_or_none()
    if run is None:
        raise LookupError(f"no run {run_id}")
    if run.finished_at is not None:
        raise ValueError(f"run {run_id} is finished")
    return run


def add_left(db, run_id: int, part: int, rows: list[dict], store) -> dict:
    """Record one part of what the plan leaves as it is (conflicts, held rows, skips, archived projects).

    Conflicts and held rows go to ``vision_sync_changes`` (the decisions page
    reads them); the whole part goes to the store. A part already recorded is
    not recorded again.

    Args:
        db: An open session (committed here).
        run_id: The run.
        part: The part's number (1, 2, ...).
        rows: Report rows (see :func:`apply.report_row`).
        store: The log store.

    Returns:
        ``{"log", "rows", "in_db"}``, plus ``"repeat": True`` when already recorded.
    """
    from sqlalchemy import insert
    from models import VisionSyncChange
    from .apply import log_rows
    run = lock_run(db, run_id)
    totals = _totals(run)
    if str(part) in totals.get("left", {}):
        db.rollback()
        return {**totals["left"][str(part)], "repeat": True}
    entries = log_rows(rows)
    keep = [{**e, "run_id": run_id} for e in entries if e["action"] in DB_ACTIONS]
    for i in range(0, len(keep), 2000):
        db.execute(insert(VisionSyncChange), keep[i:i + 2000])
    key = log_key(run_id, f"left-{part:04d}")
    store.put_json(key, entries)
    totals.setdefault("left", {})[str(part)] = result = {"log": key, "rows": len(entries), "in_db": len(keep)}
    run.totals = totals
    try:
        db.commit()
    except Exception:
        db.rollback()
        store.delete(key)                                  # no log for something not recorded
        raise
    return result


def run_batch(db, run_id: int, batch: dict, store) -> tuple[dict, list[dict], list[dict]]:
    """Apply one plan batch in one transaction and log it.

    Args:
        db: An open session (committed or rolled back here).
        run_id: The run (see :func:`start_run`).
        batch: A decoded plan batch; its ``plan`` must be the run's plan hash.
        store: The log store.

    Returns:
        ``(result, entries, report)``: the batch's record on the run (log key,
        action counts), its log entries and its report rows (both empty when
        the batch was applied already).

    Raises:
        ValueError: The batch belongs to another plan, or the run is finished.
    """
    from auth import current_actor
    from .apply import ACTOR, apply, log_rows
    run = lock_run(db, run_id)                             # one batch at a time
    totals = _totals(run)
    if batch.get("plan") != totals.get("plan"):
        db.rollback()
        raise ValueError("this batch belongs to another plan")
    done = totals.get("batches", {}).get(batch["key"])
    if done:
        db.rollback()
        return {**done, "repeat": True}, [], []             # applied before: never twice
    token = current_actor.set(ACTOR)                       # snapshots name the tool as the actor
    try:
        report, _ = apply(db, delta_of(batch), {}, None, planned=True)
    finally:
        current_actor.reset(token)
    entries = log_rows(report)
    actions = Counter(e["action"] for e in entries)
    key = log_key(run_id, f"batch-{batch['key']}")
    store.put_json(key, entries)                           # the log first: no change without one
    result = {"log": key, "at": datetime.utcnow().isoformat(timespec="seconds"), "projects": len(batch["projects"]),
              "actions": dict(actions)}
    totals.setdefault("batches", {})[batch["key"]] = result
    run.totals = totals
    try:
        db.commit()
    except Exception:
        db.rollback()
        store.delete(key)                                  # the batch did not happen: drop its log
        raise
    return result, entries, report


def finish_run(db, run_id: int) -> dict:
    """Close the run: counts after, action totals, finish time.

    Args:
        db: An open session (committed here).
        run_id: The run.

    Returns:
        The run's totals.
    """
    from .apply import table_counts
    run = lock_run(db, run_id)
    totals = _totals(run)
    actions = Counter()
    for part in list(totals.get("batches", {}).values()):
        actions.update(part.get("actions", {}))
    totals["actions"] = dict(actions)
    run.totals = totals
    run.counts_after = table_counts(db)
    run.finished_at = datetime.utcnow()
    db.commit()
    return totals


# --------------------------------------------------------------------------- #
# Pre-flight: room for the next batch
# --------------------------------------------------------------------------- #

def preflight(db) -> dict:
    """What the database holds now, read-only (needs a superuser or ``pg_monitor``).

    Args:
        db: An open session; rolled back afterwards.

    Returns:
        ``db_mb``, ``wal_dir_mb``, ``wal_since_checkpoint_mb``,
        ``archive_mode`` and ``archive_ready`` (WAL files waiting for the archiver).
    """
    from sqlalchemy import text
    q = lambda sql: db.execute(text(sql)).scalar()
    try:
        out = {
            "db_mb": round(q("SELECT pg_database_size(current_database())") / MB, 1),
            "wal_dir_mb": round(float(q("SELECT coalesce(sum(size), 0) FROM pg_ls_waldir()")) / MB, 1),
            "wal_since_checkpoint_mb": round(float(q(
                "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), redo_lsn) FROM pg_control_checkpoint()")) / MB, 1),
            "archive_mode": q("SHOW archive_mode"),
        }
        out["archive_ready"] = (q("SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready'")
                                if out["archive_mode"] != "off" else 0)
    finally:
        db.rollback()                                      # nothing held open
    return out


def budget() -> dict:
    """The limits a batch must stay under (environment, with safe defaults for a 1 GB volume)."""
    return {
        "wal_budget_mb": float(os.getenv("VISION_CUTOVER_WAL_BUDGET_MB", "128")),
        "disk_limit_mb": float(os.getenv("VISION_CUTOVER_DISK_LIMIT_MB", "650")),
        "archive_ready_max": int(os.getenv("VISION_CUTOVER_ARCHIVE_READY_MAX", "8")),
    }


def over_budget(now: dict, estimate: Optional[dict], limits: dict) -> list[str]:
    """Why the next batch must wait, or an empty list when it may run.

    Args:
        now: :func:`preflight` output.
        estimate: The batch's local measurement (``wal_bytes``, ``db_bytes``);
            None when it was not measured (then it is refused).
        limits: :func:`budget` output.

    Returns:
        One reason per limit the batch would pass.
    """
    if not estimate or estimate.get("wal_bytes") is None:
        return ["the batch was not measured on a local copy"]
    wal = estimate["wal_bytes"] * SAFETY / MB
    grow = max(estimate.get("db_bytes") or 0, 0) * SAFETY / MB
    out = []
    if now["wal_since_checkpoint_mb"] + wal > limits["wal_budget_mb"]:
        out.append(f"WAL since the last checkpoint {now['wal_since_checkpoint_mb']} MB + batch {wal:.1f} MB "
                   f"> {limits['wal_budget_mb']} MB: wait for the next checkpoint")
    if now["archive_ready"] > limits["archive_ready_max"]:
        out.append(f"{now['archive_ready']} WAL files wait for the archiver (max {limits['archive_ready_max']})")
    if now["db_mb"] + now["wal_dir_mb"] + grow + wal > limits["disk_limit_mb"]:
        out.append(f"database {now['db_mb']} MB + WAL {now['wal_dir_mb']} MB + batch {grow + wal:.1f} MB "
                   f"> {limits['disk_limit_mb']} MB disk limit")
    return out
