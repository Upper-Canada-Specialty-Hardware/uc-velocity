"""Feedback thread proxy: relays a signed-in Velocity user's feedback to the shared
UCSH telemetry service and reads their thread back.

Why a proxy rather than calling the service from the browser:
  * the ingest key stays server-side (never shipped in the JS bundle), and the
    telemetry service needs no CORS origin for Velocity;
  * the browser can't be trusted to pick the thread key, so the backend derives a
    stable, opaque per-user key from the Clerk id (see ``feedback_thread_key``). That
    key follows the user across devices and can't be guessed, which is exactly the
    isolation the telemetry service assumes of its per-install threads.

Flow: user types a note in-app -> POST /feedback/submit -> lands on the telemetry
dashboard's board -> a dev replies there (signed in via the org GitHub OAuth app) ->
the app polls GET /feedback/threads and shows the reply -> the user can answer via
POST /feedback/reply.

Inert when telemetry is unconfigured: /config reports ``enabled: false`` (the UI then
hides the widget) and the write routes return 503.
"""
from typing import Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from auth import current_actor
from telemetry_client import (
    APP_VERSION,
    TELEMETRY_ENABLED,
    TELEMETRY_KEY,
    TELEMETRY_SOURCE,
    TELEMETRY_URL,
    feedback_thread_key,
)

router = APIRouter(prefix="/feedback", tags=["feedback"])

_TIMEOUT = 10.0


class FeedbackSubmit(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    title: Optional[str] = Field(default=None, max_length=200)
    category: Optional[str] = None


class FeedbackReplySubmit(BaseModel):
    feedback_id: int
    message: str = Field(min_length=1, max_length=5000)


def _actor() -> dict:
    """The signed-in Clerk user, or 401. Feedback is always tied to a real person."""
    actor = current_actor.get()
    if not actor or not actor.get("user_id"):
        raise HTTPException(status_code=401, detail="Sign in to use feedback.")
    return actor


def _headers() -> dict:
    return {"Authorization": f"Bearer {TELEMETRY_KEY}", "Content-Type": "application/json"}


@router.get("/config")
def feedback_config() -> dict:
    """Whether feedback is switched on. The frontend hides the widget when False."""
    return {"enabled": TELEMETRY_ENABLED}


@router.post("/submit")
async def submit_feedback(payload: FeedbackSubmit) -> dict:
    if not TELEMETRY_ENABLED:
        raise HTTPException(status_code=503, detail="Feedback is not configured.")
    actor = _actor()
    body = {
        "source": TELEMETRY_SOURCE,
        "app_version": APP_VERSION,
        "install_id": feedback_thread_key(actor["user_id"]),
        "user_id": actor["user_id"],
        "user_name": actor.get("email"),
        "category": payload.category,
        "title": payload.title,
        "message": payload.message.strip(),
        "event_id": str(uuid4()),
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(f"{TELEMETRY_URL}/feedback", json=body, headers=_headers())
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not submit feedback right now.")
    return {"ok": True}


@router.get("/threads")
async def get_threads() -> dict:
    """This user's own feedback notes, each with its dev/user replies."""
    if not TELEMETRY_ENABLED:
        return {"threads": []}
    actor = _actor()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get(
            f"{TELEMETRY_URL}/feedback/threads",
            params={"install_id": feedback_thread_key(actor["user_id"])},
            headers=_headers(),
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not load your feedback.")
    return r.json()


@router.post("/reply")
async def reply(payload: FeedbackReplySubmit) -> dict:
    if not TELEMETRY_ENABLED:
        raise HTTPException(status_code=503, detail="Feedback is not configured.")
    actor = _actor()
    body = {
        "source": TELEMETRY_SOURCE,
        "install_id": feedback_thread_key(actor["user_id"]),
        "user_id": actor["user_id"],
        "user_name": actor.get("email"),
        "feedback_id": payload.feedback_id,
        "message": payload.message.strip(),
        "event_id": str(uuid4()),
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.post(f"{TELEMETRY_URL}/feedback/reply", json=body, headers=_headers())
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail="Could not send your reply.")
    return {"ok": True}
