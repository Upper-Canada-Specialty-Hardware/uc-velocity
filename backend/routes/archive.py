"""Archived projects: the read side, plus unarchiving (#304 archive, #319 database index + restore).

The archive *file* still lives in the store (see
``scripts/vision_delta/archive.py``); what changed in #319 is that the list and
lookups now read the ``archived_projects`` table (filled by the admin import
route and the archive-by-hand route) instead of the store's ``index.json``, so a
search is a normal indexed query. A project's file is still read from the store
when it is opened, by the row's ``file_key``.

Routes (under ``/archive``):
    GET  /projects                  search the archive (UCA number, name, customer)
    GET  /projects/{uca}            one archived project: Vision's version and Velocity's copy
    POST /projects/{uca}/unarchive  put an archived project back (signed-in)
"""
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

import log_store
from auth import current_actor
from database import get_db
from models import ArchivedProjectRow
from scripts.vision_delta.archive import PREFIX, RestoreError, mark_closed, restore_project

router = APIRouter(prefix="/archive", tags=["archive"])


def get_store():
    """The store (bucket, or local folder) the archive files are in.

    Raises:
        HTTPException: 503 when none is configured.
    """
    store = log_store.from_env()
    if store is None:
        raise HTTPException(status_code=503, detail="the archive store is not configured")
    return store


def _actor() -> str:
    """Who is acting; refuses an anonymous request (same rule as the decisions page).

    Raises:
        HTTPException: 401 when no signed-in user is attached.
    """
    a = current_actor.get()
    if not a:
        raise HTTPException(status_code=401, detail="sign in to archive or unarchive a project")
    return a.get("email") or a["user_id"]                  # email for people, id as a fallback


def _entry(r: ArchivedProjectRow) -> dict:
    """One archived project as the list shows it (the shape the old index.json held)."""
    return {"uca": r.uca, "name": r.name, "customer": r.customer, "created_on": r.created_on,
            "last_activity": r.last_activity, "work_orders": r.work_orders, "pos": r.pos,
            "velocity_quotes": r.velocity_quotes, "velocity_pos": r.velocity_pos,
            "in_velocity": r.in_velocity, "key": r.file_key}


@router.get("/projects")
def search(q: Optional[str] = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
           all: bool = Query(False), db: Session = Depends(get_db)) -> dict:
    """Archived projects whose UCA number, name or customer contains ``q`` (all when empty), newest activity first.

    With ``all=true`` every matching row is returned in one response (no paging),
    so the browse-all page (#326) can hold the whole archive (~2,145 rows) and sort
    and filter it client-side; ``offset``/``limit`` are ignored then.
    """
    base = db.query(ArchivedProjectRow).filter(ArchivedProjectRow.state == "archived")
    archived = base.count()                                # how many are archived in all (for the header)
    term = (q or "").strip()
    rows = base
    if term:                                               # match any of the three text fields, case-insensitively
        like = f"%{term}%"
        rows = rows.filter(or_(ArchivedProjectRow.uca.ilike(like), ArchivedProjectRow.name.ilike(like),
                               ArchivedProjectRow.customer.ilike(like)))
    total = rows.count()
    ordered = rows.order_by(ArchivedProjectRow.last_activity.desc().nullslast(), ArchivedProjectRow.uca)
    page = ordered.all() if all else ordered.offset(offset).limit(limit).all()
    return {"total": total, "archived": archived, "projects": [_entry(r) for r in page]}


@router.get("/projects/{uca}")
async def project(uca: str, db: Session = Depends(get_db), store=Depends(get_store)) -> dict:
    """One archived project's file, found by the UCA's current archived row.

    Raises:
        HTTPException: 400 on a malformed number, 404 when it is not archived.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", uca):      # a UCA number, never a path
        raise HTTPException(status_code=400, detail="bad project number")
    row = (db.query(ArchivedProjectRow).filter(ArchivedProjectRow.uca == uca, ArchivedProjectRow.state == "archived")
           .order_by(ArchivedProjectRow.archived_at.desc().nullslast(), ArchivedProjectRow.id.desc()).first())
    if row is None:
        raise HTTPException(status_code=404, detail=f"{uca} is not in the archive")
    try:
        return await run_in_threadpool(store.get_json, row.file_key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"{uca}'s archive file is missing from the store") from e


@router.post("/projects/{uca}/unarchive")
async def unarchive(uca: str, db: Session = Depends(get_db), store=Depends(get_store)) -> dict:
    """Put an archived project back (signed-in). 409 with reasons when it cannot be restored.

    When the live rows were never removed (the bulk removal had not reached it) the
    project is still in the database, so this only flips the row to ``restored``.
    Otherwise every Velocity row is re-inserted from the file with its original ids,
    verified, and the bulk removal marker is cleared. Either way the project comes
    back with status ``closed`` (#330). A Vision-only archive has no Velocity rows
    and cannot be unarchived.

    Raises:
        HTTPException: 400 bad number, 401 no actor, 404 not archived, 409 cannot restore.
    """
    who = _actor()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", uca):
        raise HTTPException(status_code=400, detail="bad project number")
    row = (db.query(ArchivedProjectRow).filter(ArchivedProjectRow.uca == uca, ArchivedProjectRow.state == "archived")
           .order_by(ArchivedProjectRow.archived_at.desc().nullslast(), ArchivedProjectRow.id.desc())
           .with_for_update().first())
    if row is None:
        raise HTTPException(status_code=404, detail=f"{uca} is not in the archive")
    try:
        f = await run_in_threadpool(store.get_json, row.file_key)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"{uca}'s archive file is missing from the store") from e
    if not f.get("velocity") or f.get("velocity_project_id") is None:   # only ever in Vision: nothing to restore
        raise HTTPException(status_code=409, detail={"reasons": ["exists only in Vision"]})

    pid = f["velocity_project_id"]
    if not row.rows_removed:                               # the live rows are still there: just flip the row back
        row.state, row.restored_by, row.restored_at = "restored", who, datetime.utcnow()
        mark_closed(db, pid)                               # back as closed either way (#330), same commit
        db.commit()
        return {"uca": uca, "project_id": pid, "reinserted": False, "restored": {}}

    def work():
        return restore_project(db, f)                      # re-insert, verify, all in this transaction

    try:
        counts = await run_in_threadpool(work)
    except RestoreError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail={"reasons": e.reasons}) from e
    row.state, row.restored_by, row.restored_at = "restored", who, datetime.utcnow()
    db.commit()
    await run_in_threadpool(store.delete, f"{PREFIX}/removed/{uca}.json")   # a later bulk run or re-archive is not confused
    return {"uca": uca, "project_id": pid, "reinserted": True, "restored": counts}
