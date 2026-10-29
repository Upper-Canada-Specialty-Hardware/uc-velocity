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
