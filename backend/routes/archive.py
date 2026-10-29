"""Read-only access to archived projects (#304): projects with no activity since 2018.

The archive lives in the bucket (see ``scripts/vision_delta/archive.py``), not
in Postgres. The index is read once and kept for a few minutes; a project's
file is read from the bucket when it is opened (slower than a normal project,
but rare).

Routes (under ``/archive``):
    GET /projects          search the index (UCA number, name, customer)
    GET /projects/{uca}    one archived project: Vision's version and Velocity's copy
"""
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

import log_store
from scripts.vision_delta.archive import PREFIX

router = APIRouter(prefix="/archive", tags=["archive"])

INDEX_TTL = 300                                            # seconds the index is kept in memory
_index: dict = {"at": 0.0, "data": None}                   # cached index (per worker)


def get_store():
    """The bucket (or local folder) the archive is in.

    Raises:
        HTTPException: 503 when none is configured.
    """
    store = log_store.from_env()
    if store is None:
        raise HTTPException(status_code=503, detail="the archive store is not configured")
    return store


def _load_index(store) -> list[dict]:
    """The archive index, from memory when fresh, else from the store (empty when there is no archive yet)."""
    if _index["data"] is None or time.time() - _index["at"] > INDEX_TTL:
        try:
            _index["data"] = store.get_json(f"{PREFIX}/index.json")["projects"]
        except KeyError:
            return []                                      # nothing archived yet: not cached, so a new archive shows at once
        _index["at"] = time.time()
    return _index["data"]


@router.get("/projects")
async def search(q: Optional[str] = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
                 store=Depends(get_store)) -> dict:
    """Archived projects whose UCA number, name or customer contains ``q`` (all when empty), newest activity first."""
    index = await run_in_threadpool(_load_index, store)
    term = (q or "").strip().lower()
    hits = [p for p in index if not term or any(term in str(p.get(k) or "").lower() for k in ("uca", "name", "customer"))]
    hits.sort(key=lambda p: str(p.get("last_activity") or ""), reverse=True)
    return {"total": len(hits), "archived": len(index), "projects": hits[offset:offset + limit]}


@router.get("/projects/{uca}")
async def project(uca: str, store=Depends(get_store)) -> dict:
    """One archived project's file.

    Raises:
        HTTPException: 400 on a malformed number, 404 when it is not archived.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", uca):     # a UCA number, never a path
        raise HTTPException(status_code=400, detail="bad project number")
    try:
        return await run_in_threadpool(store.get_json, f"{PREFIX}/projects/{uca}.json")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"{uca} is not in the archive") from e
