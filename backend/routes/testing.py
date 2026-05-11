"""Endpoints that support agentic UI testing.

Gated by `TESTING_ENABLED=1`. When the gate is off, every endpoint here
returns 404 so the surface is invisible in production. The Clerk
sign-in-token flow uses the Backend Admin API
(https://api.clerk.com/v1) with `CLERK_SECRET_KEY` and mints a
one-time ticket that the frontend's ClerkProvider consumes via the
`?__clerk_ticket=` query param.
"""

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/testing", tags=["testing"])

_CLERK_API = "https://api.clerk.com/v1"


def _testing_enabled() -> bool:
    return os.getenv("TESTING_ENABLED") == "1"


def _allowed_emails() -> set[str]:
    raw = os.getenv("TESTING_ALLOWED_EMAILS", "jayp@ucsh.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _require_testing_enabled() -> None:
    if not _testing_enabled():
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/status")
def status() -> dict[str, bool]:
    """Diagnostic: whether the testing surface is live and Clerk wired.

    Always returns 200 — even when disabled — so an agent can probe
    setup state. Reveals only two booleans, no secrets.
    """
    return {
        "testing_enabled": _testing_enabled(),
        "clerk_configured": bool(os.getenv("CLERK_SECRET_KEY")),
    }


@router.get("/clerk-sign-in")
def clerk_sign_in(email: str = Query(..., min_length=3)) -> dict[str, Any]:
    """Mint a one-time Clerk sign-in ticket for an allowlisted email.

    Returns `ticket_url_query` — append it to the frontend root URL and
    Clerk's frontend SDK will auto-sign-in. Ticket expires in 10 minutes
    and is single-use.
    """
    _require_testing_enabled()
    if email.lower() not in _allowed_emails():
        raise HTTPException(status_code=403, detail="Email not in test-user allowlist")

    secret = os.getenv("CLERK_SECRET_KEY")
    if not secret:
        raise HTTPException(status_code=503, detail="CLERK_SECRET_KEY not configured")

    headers = {"Authorization": f"Bearer {secret}"}
    with httpx.Client(base_url=_CLERK_API, timeout=10.0, headers=headers) as client:
        r = client.get("/users", params={"email_address": email, "limit": 1})
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Clerk user lookup failed: {r.status_code} {r.text[:200]}",
            )
        users = r.json()
        if not users:
            raise HTTPException(status_code=404, detail=f"No Clerk user with email {email}")
        user_id = users[0]["id"]

        r = client.post(
            "/sign_in_tokens",
            json={"user_id": user_id, "expires_in_seconds": 600},
        )
        if r.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Clerk sign-in-token failed: {r.status_code} {r.text[:200]}",
            )
        data = r.json()

    return {
        "ticket": data["token"],
        "ticket_url_query": f"?__clerk_ticket={data['token']}",
        "user_id": user_id,
        "expires_at": data.get("expires_at"),
    }
