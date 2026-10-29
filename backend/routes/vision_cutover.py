"""Admin API that applies a confirmed Vision cutover plan to this database, one batch at a time.

The plan is made and measured on a local copy (scripts/vision_delta/plan.py)
and sent here by scripts/vision_delta/push.py. Nothing is compared or decided
here; each batch is applied as planned (see scripts/vision_delta/runner.py).

Off unless ``VISION_CUTOVER_KEY`` is set: every route then answers 404. Each
request must carry that key in ``X-Cutover-Key``. Unset the variable after
the cutover to turn the API off again.

Routes (all under ``/admin/vision-cutover``):
    GET  /preflight              database and WAL figures, and the limits
    POST /runs                   open (or resume) the run for a plan
    POST /runs/{id}/left/{part}  record part of what the plan leaves as it is
    POST /runs/{id}/batches      apply one batch (409 when the pre-flight check says wait)
    POST /runs/{id}/finish       close the run
    GET  /runs/{id}/log/{name}   one of the run's log objects (for verify.py's local copy)
    PUT  /archive/files          write archive files to the bucket and read each back (#304)
    POST /archive/remove         remove archived projects' rows, a few at a time (409 when it must wait)
"""
import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from database import get_db
import log_store
from scripts.vision_delta import runner

router = APIRouter(prefix="/admin/vision-cutover", tags=["admin"])


def require_key(x_cutover_key: str = Header(default="")) -> None:
    """Let a request through only with the cutover key.

    Raises:
        HTTPException: 404 when the API is off (no key configured), 403 on a wrong key.
    """
    key = os.getenv("VISION_CUTOVER_KEY", "")
    if not key:
        raise HTTPException(status_code=404, detail="Not Found")   # off: looks like no route at all
    if not hmac.compare_digest(x_cutover_key.encode(), key.encode()):   # constant time
        raise HTTPException(status_code=403, detail="wrong cutover key")


def get_store():
    """The configured log store.

    Raises:
        HTTPException: 503 when no bucket (or folder) is configured.
    """
    store = log_store.from_env()
    if store is None:
        raise HTTPException(status_code=503, detail="no log store: set the bucket variables (BUCKET, ...)")
    return store


async def body_of(request: Request):
    """The request's JSON body, with the plan's dates and decimals restored.

    Raises:
        HTTPException: 400 on a body that is not JSON.
    """
    try:
        return runner.decode(await request.body())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad JSON: {e}") from e


@router.get("/preflight", dependencies=[Depends(require_key)])
def preflight(db: Session = Depends(get_db)):
    """Current database, WAL and archiver figures, and the limits batches must stay under."""
    return {"now": runner.preflight(db), "limits": runner.budget()}


@router.post("/runs", dependencies=[Depends(require_key)])
async def open_run(request: Request, db: Session = Depends(get_db)):
    """Open the run for a plan, or return the open run of the same plan.

    Body: ``{"meta": <plan meta>, "operator": "<who runs it>"}``.

    Returns:
        ``{"run_id", "created"}``.
    """
    from scripts.vision_delta.apply import SCHEMA
    from sqlalchemy import text
    body = await body_of(request)
    version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    if version != SCHEMA:                                  # the plan's columns and log tables must exist
        raise HTTPException(status_code=409, detail=f"database is at {version}, not {SCHEMA}")
    try:
        run_id, created = await run_in_threadpool(runner.start_run, db, body["meta"], str(body.get("operator") or "push"))
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"run_id": run_id, "created": created}


@router.post("/runs/{run_id}/left/{part}", dependencies=[Depends(require_key)])
async def add_left(run_id: int, part: int, request: Request, db: Session = Depends(get_db),
                   store=Depends(get_store)):
    """Record one part of the rows the plan leaves as they are (conflicts and held rows also in Postgres).

    Body: a list of report rows.
    """
    rows = await body_of(request)
    try:
        return await run_in_threadpool(runner.add_left, db, run_id, part, rows, store)   # off the event loop
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/runs/{run_id}/batches", dependencies=[Depends(require_key)])
async def apply_batch(run_id: int, request: Request, db: Session = Depends(get_db), store=Depends(get_store)):
    """Apply one batch, after the pre-flight check.

    Body: one plan batch.

    Returns:
        ``{"result", "entries"}``: the batch's record on the run and its change
        log (push.py keeps a local copy). A batch applied before returns its
        record with ``repeat`` and no entries.

    Raises:
        HTTPException: 409 with the reasons when the batch must wait (push.py
            waits and retries), 404 for no such run, 400 for a batch of another plan.
    """
    batch = await body_of(request)
    if not isinstance(batch, dict) or not {"key", "plan", "rows"} <= batch.keys():
        raise HTTPException(status_code=400, detail="not a plan batch")
    try:
        now = await run_in_threadpool(runner.preflight, db)
    except Exception as e:                                 # noqa: BLE001 -- refuse when the figures cannot be read
        raise HTTPException(status_code=503, detail=f"pre-flight figures unavailable: {type(e).__name__}: {e}") from e
    wait = runner.over_budget(now, batch.get("estimate"), runner.budget())
    if wait:
        raise HTTPException(status_code=409, detail={"wait": wait, "now": now})
    try:
        result, entries, _ = await run_in_threadpool(runner.run_batch, db, run_id, batch, store)   # off the event loop
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return runner.encode({"result": result, "entries": entries, "now": now})


@router.post("/runs/{run_id}/finish", dependencies=[Depends(require_key)])
def finish(run_id: int, db: Session = Depends(get_db)):
    """Close the run (counts after, action totals)."""
    try:
        return runner.finish_run(db, run_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/runs/{run_id}/log/{name}", dependencies=[Depends(require_key)])
def get_log(run_id: int, name: str, store=Depends(get_store)):
    """One of the run's log objects from the store (``batch-0001``, ``left-0001``), for a local copy.

    Raises:
        HTTPException: 400 on a bad name, 404 when there is no such object.
    """
    if not name.replace("-", "").isalnum():                # only the names runner.log_key makes
        raise HTTPException(status_code=400, detail="bad log name")
    try:
        return runner.encode(store.get_json(runner.log_key(run_id, name)))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"no log {name} for run {run_id}") from e


# ---- the decisions page's cases, built and loaded here (#297) ----------- #

CASES_SCHEMA = "035_vision_decisions"                      # the vision_cases table exists from this migration


@router.post("/cases/load", dependencies=[Depends(require_key)])
async def load_cases(request: Request, db: Session = Depends(get_db)):
    """Build a cutover run's decision cases on the server and insert the ones it lacks (#297).

    The change-row cases (test projects, credit memos, conflicts) are built
    here from this database's ``vision_sync_changes`` (actions a person
    settles, see ``cases.CHANGE_ACTIONS``). The findings cases (same job, same
    PO, duplicate projects, Velocity copies) are read from the plan's
    ``report.xlsx`` on the client and sent in the body, so no production
    database URL is needed there. Loading is insert-only and idempotent, as
    ``cases.load`` is: running it again changes nothing.

    Body: ``{"run_id": int, "findings": [case dicts], "commit": bool}``.

    Returns:
        ``{"counts": {kind: how many cases}, "new": how many inserted, "committed": bool}``.

    Raises:
        HTTPException: 409 when the database predates ``035_vision_decisions``, 404
            for no such run, 400 on a bad body.
    """
    from sqlalchemy import text
    from scripts.vision_delta import cases as cs
    from models import VisionSyncChange, VisionSyncRun
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("run_id"), int):
        raise HTTPException(status_code=400, detail="run_id (int) is required")
    findings = body.get("findings") or []                  # case dicts from the client's report.xlsx
    if not isinstance(findings, list):
        raise HTTPException(status_code=400, detail="findings must be a list of case dicts")
    run_id, commit = body["run_id"], bool(body.get("commit"))
    if db.execute(text("SELECT to_regclass('vision_cases')")).scalar() is None:   # table made by 035 and kept after
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
        raise HTTPException(status_code=409, detail=f"database is at {version}, before {CASES_SCHEMA}")

    def work():
        if db.get(VisionSyncRun, run_id) is None:          # a run id the cutover never opened
            raise LookupError(f"no run {run_id}")
        cols = ("id", "level", "action", "record_table", "record_id", "vision_key", "project", "context",
                "field", "before", "after", "reason")      # the fields change_cases groups on
        q = db.query(*(getattr(VisionSyncChange, c) for c in cols)).filter(
            VisionSyncChange.run_id == run_id, VisionSyncChange.action.in_(cs.CHANGE_ACTIONS))
        built = cs.change_cases(dict(zip(cols, r)) for r in q)   # cases from this database's own log rows
        cases = built + list(findings)                     # plus the findings the client read from the report
        counts: dict[str, int] = {}
        for c in cases:                                    # how many cases of each kind, for the operator
            counts[c["kind"]] = counts.get(c["kind"], 0) + 1
        new = cs.load(db, run_id, cases, commit)           # insert-only; rolls back when commit is False
        return {"counts": counts, "new": new, "committed": commit}

    try:
        return await run_in_threadpool(work)               # off the event loop: it queries and may write
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ---- the archive of projects with no activity since 2018 (#304) --------- #

ARCHIVE_PUT_MAX = 100                                      # files per request
ARCHIVE_REMOVE_MAX = 50                                    # projects per request
ARCHIVE_BUILD_MAX = 50                                     # projects built per request


def _archive_key(key: str) -> str:
    """Refuse keys outside the archive's prefix.

    Raises:
        HTTPException: 400 on any other key.
    """
    from scripts.vision_delta.archive import PREFIX
    if not isinstance(key, str) or not key.startswith(PREFIX + "/") or ".." in key or not key.endswith(".json"):
        raise HTTPException(status_code=400, detail=f"bad archive key {key!r}")
    return key


@router.put("/archive/files", dependencies=[Depends(require_key)])
async def put_archive_files(request: Request, store=Depends(get_store)):
    """Write archive files to the store and read every one back.

    Body: ``{"files": {key: object}}`` (at most 100).

    Returns:
        ``{"written": {key: sha256 of what reads back}}``.

    Raises:
        HTTPException: 400 on a bad key or too many files, 502 when a file does not read back the same.
    """
    from scripts.vision_delta.archive import sha
    try:
        files = (await request.json())["files"]
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(status_code=400, detail="body must be {\"files\": {key: object}}") from e
    if not isinstance(files, dict) or len(files) > ARCHIVE_PUT_MAX:
        raise HTTPException(status_code=400, detail=f"send 1 to {ARCHIVE_PUT_MAX} files")
    written = {}
    for key, obj in files.items():
        _archive_key(key)

        def put_and_read(k=key, o=obj):
            store.put_json(k, o)
            return store.get_json(k)                       # read back: the bucket holds exactly this
        back = await run_in_threadpool(put_and_read)
        if sha(back) != sha(obj):
            raise HTTPException(status_code=502, detail=f"{key} did not read back the same")
        written[key] = sha(back)
    return {"written": written}


@router.post("/archive/build", dependencies=[Depends(require_key)])
async def build_archive(request: Request, db: Session = Depends(get_db), store=Depends(get_store)):
    """Build archive files for a batch of old projects and write them to the store (#304).

    For each project the server finds its Velocity project the way
    ``archive.select`` does (the one whose
    ``coalesce(vision_project_number, uca_project_number)`` equals the UCA
    number; more than one is refused), checks nobody touched it in Velocity
    since June (an open cutover decision counts), and, when untouched, builds
    the file -- Vision's version from the request, Velocity's rows read here --
    writes it under ``vision-archive/projects/<uca>.json`` and reads it back.
    This route reads the database only (a read-only transaction); the client
    picked the old projects, so no production database URL is needed there.

    Body: ``{"projects": [{"uca", "last_activity", "vision": <vision_version>}...],
    "active_since": "YYYY-MM-DD", "june_day": "YYYY-MM-DD"}`` (at most 50 projects).

    Returns:
        ``{"projects": {uca: {"key", "sha256", "velocity_project_id", "rows",
        "index"} when built, or {"kept": [reasons]} when it stays}}``. The
        client collects the index and manifest entries and sends them to
        ``/archive/finish``.

    Raises:
        HTTPException: 400 on a bad body, a bad UCA number or too many
            projects, 502 when a file does not read back the same.
    """
    import re
    from datetime import date
    from sqlalchemy import text
    from scripts.vision_delta.archive import (PREFIX, build_file_parts, index_entry, sha, touched_projects,
                                              velocity_rows)
    body = await request.json()
    projects = body.get("projects") if isinstance(body, dict) else None
    if not isinstance(projects, list) or not projects or len(projects) > ARCHIVE_BUILD_MAX:
        raise HTTPException(status_code=400, detail=f"send 1 to {ARCHIVE_BUILD_MAX} projects")
    try:
        june_day = date.fromisoformat(body["june_day"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="june_day (YYYY-MM-DD) is required") from e
    for p in projects:                                     # a UCA number, never a path: it becomes a store key
        if not isinstance(p, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(p.get("uca") or "")):
            raise HTTPException(status_code=400, detail=f"bad project {p.get('uca') if isinstance(p, dict) else p!r}")

    def work():
        import json
        db.execute(text("SET TRANSACTION READ ONLY"))     # this route never writes the database
        out = {}
        for p in projects:
            uca = p["uca"]
            key = _archive_key(f"{PREFIX}/projects/{uca}.json")
            try:                                           # already removed: its rows are gone from the database,
                store.get_json(f"{PREFIX}/removed/{uca}.json")   # so rebuilding would drop Velocity's copy
                done = store.get_json(key)                 # keep the stored file exactly as it was
                out[uca] = {"key": key, "sha256": sha(done), "velocity_project_id": done.get("velocity_project_id"),
                            "rows": {t: [r["id"] for r in rs] for t, rs in (done.get("velocity") or {}).items()},
                            "index": index_entry(done, key)}
                continue
            except KeyError:
                pass                                       # not removed yet: build it from the live rows
            pids = [r[0] for r in db.execute(text(                      # the same match archive.select makes
                "SELECT id FROM projects WHERE coalesce(vision_project_number, uca_project_number) = :uca"),
                {"uca": uca})]
            if len(pids) > 1:                              # two Velocity projects share this number: never guess
                out[uca] = {"kept": ["more than one velocity project carries this number"]}
                continue
            pid = pids[0] if pids else None                # the Velocity id, or None for a Vision-only project
            touched = touched_projects(db, [pid], june_day) if pid is not None else {}   # worked on since June?
            if pid in touched:                             # a quote/PO/invoice/edit/open decision since June: keep it
                out[uca] = {"kept": touched[pid]}
                continue
            vel = velocity_rows(db, pid) if pid is not None else None   # Velocity's rows for the file
            doc = build_file_parts(uca, p.get("last_activity"), pid, p.get("vision") or {}, vel)
            doc = json.loads(json.dumps(doc, default=str))  # exactly what the file will hold (dates as strings)
            store.put_json(key, doc)
            if sha(store.get_json(key)) != sha(doc):        # read back: the store holds exactly this
                raise ValueError(f"{key} did not read back the same")
            out[uca] = {"key": key, "sha256": sha(doc), "velocity_project_id": pid,
                        "rows": {t: [r["id"] for r in rs] for t, rs in (vel or {}).items()},
                        "index": index_entry(doc, key)}
        db.rollback()                                      # nothing held open; the route wrote no database rows
        return out

    try:
        built = await run_in_threadpool(work)              # off the event loop: it queries and writes the store
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"projects": built}


@router.post("/archive/finish", dependencies=[Depends(require_key)])
async def finish_archive(request: Request, store=Depends(get_store)):
    """Write the archive index and manifest from the collected build results, manifest last (#304).

    Until the manifest is in the store nothing can be removed, so it is written
    after the index. Call this once, after every ``/archive/build``.

    Body: ``{"meta": {...}, "index": [index entries], "manifest": {key:
    {"sha256", "velocity_project_id", "rows"}}}``.

    Returns:
        ``{"index": how many projects, "files": how many manifest entries}``.

    Raises:
        HTTPException: 400 on a bad body or a manifest key outside the archive.
    """
    from scripts.vision_delta.archive import PREFIX
    body = await request.json()
    index = body.get("index") if isinstance(body, dict) else None
    manifest = body.get("manifest") if isinstance(body, dict) else None
    if not isinstance(index, list) or not isinstance(manifest, dict):
        raise HTTPException(status_code=400, detail="index (list) and manifest (object) are required")
    for key in manifest:                                   # every manifest key must stay under the archive prefix
        _archive_key(key)
    meta = body.get("meta") or {}
    index_doc = {"meta": meta, "projects": sorted(index, key=lambda r: str(r.get("uca") or ""))}
    manifest_doc = {"meta": meta, "files": manifest}

    def work():
        store.put_json(f"{PREFIX}/index.json", index_doc)      # the index first
        store.put_json(f"{PREFIX}/manifest.json", manifest_doc)   # the manifest last: nothing removable until now

    await run_in_threadpool(work)                          # off the event loop: store writes
    return {"index": len(index), "files": len(manifest)}


@router.post("/archive/remove", dependencies=[Depends(require_key)])
async def remove_archived(request: Request, db: Session = Depends(get_db), store=Depends(get_store)):
    """Remove archived projects' rows from the database, after the pre-flight check.

    Body: ``{"keys": [archive file keys], "operator": "<who>", "june_day": "YYYY-MM-DD",
    "estimate": {"wal_bytes": n, "db_bytes": 0}}``.

    Each project is checked against its archive file (the file must match the
    manifest; its Velocity rows must still equal the database's; nobody may
    have touched it) and removed on its own: one that fails the check is left
    as it is and reported. A removal record is written to the store after the
    commit; a project already gone is recorded as removed.

    Returns:
        ``{"removed": {key: {table: rows}}, "refused": {key: reason}, "now": figures}``.

    Raises:
        HTTPException: 409 with the reasons when the batch must wait.
    """
    from datetime import date, datetime
    from sqlalchemy import text
    from scripts.vision_delta.archive import PREFIX, NotSafe, remove_project, sha
    body = await request.json()
    keys = [_archive_key(k) for k in body.get("keys", [])][:ARCHIVE_REMOVE_MAX]
    try:
        june_day = date.fromisoformat(body["june_day"])
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="june_day (YYYY-MM-DD) is required") from e
    try:
        now = await run_in_threadpool(runner.preflight, db)
    except Exception as e:                                 # noqa: BLE001 -- refuse when the figures cannot be read
        raise HTTPException(status_code=503, detail=f"pre-flight figures unavailable: {type(e).__name__}: {e}") from e
    wait = runner.over_budget(now, body.get("estimate"), runner.budget())
    if wait:
        raise HTTPException(status_code=409, detail={"wait": wait, "now": now})

    def work():
        manifest = store.get_json(f"{PREFIX}/manifest.json")["files"]
        removed, refused = {}, {}
        for key in keys:
            uca = key.rsplit("/", 1)[-1][:-5]
            marker = f"{PREFIX}/removed/{uca}.json"
            try:
                removed[key] = store.get_json(marker)["rows"]   # done before: report it again
                continue
            except KeyError:
                pass
            try:
                f = store.get_json(key)
            except KeyError:
                refused[key] = "no archive file"; continue
            if key not in manifest or sha(f) != manifest[key]["sha256"]:
                refused[key] = "the archive file does not match the manifest"; continue
            pid = f.get("velocity_project_id")
            gone = pid and db.execute(text("SELECT 1 FROM projects WHERE id = :p"), {"p": pid}).first() is None
            try:
                rows = {} if gone else remove_project(db, f, june_day)
                db.commit()
            except NotSafe as e:
                db.rollback()
                refused[key] = str(e); continue
            store.put_json(marker, {"uca": uca, "rows": rows, "already_gone": bool(gone),
                                    "operator": str(body.get("operator") or "push"),
                                    "at": datetime.utcnow().isoformat(sep=" ")})
            removed[key] = rows
        return removed, refused

    removed, refused = await run_in_threadpool(work)
    return {"removed": removed, "refused": refused, "now": now}
