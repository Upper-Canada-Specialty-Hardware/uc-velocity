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


# ---- the archive of projects with no activity since 2018 (#304) --------- #

ARCHIVE_PUT_MAX = 100                                      # files per request
ARCHIVE_REMOVE_MAX = 50                                    # projects per request


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
