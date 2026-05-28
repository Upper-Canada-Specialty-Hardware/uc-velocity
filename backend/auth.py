"""Best-effort Clerk JWT verification for the audit trail.

A pure-ASGI middleware verifies the incoming Clerk session token
(``Authorization: Bearer ...``) against the issuer's JWKS and stashes the acting
user's id/email in a request-scoped contextvar. The snapshot helpers
(``create_snapshot`` / ``create_po_snapshot``) read that contextvar to record
*who* performed each action.

Verification is intentionally best-effort: a missing or invalid token simply
yields no actor and never blocks the request, so the audit trail is purely
additive and cannot break existing functionality.
"""
import contextvars
import os
from typing import Optional

import httpx
import jwt
from jwt import PyJWKClient
from starlette.concurrency import run_in_threadpool

# Request-scoped acting user: {"user_id": str, "email": Optional[str]} or None.
current_actor: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "current_actor", default=None
)

# Caches: JWKS client per issuer, and resolved email per Clerk user id.
_jwks_clients: dict[str, PyJWKClient] = {}
_email_cache: dict[str, Optional[str]] = {}


def _jwks_client_for(issuer: str) -> PyJWKClient:
    url = f"{issuer.rstrip('/')}/.well-known/jwks.json"
    client = _jwks_clients.get(url)
    if client is None:
        client = PyJWKClient(url)
        _jwks_clients[url] = client
    return client


def _resolve_email(user_id: str) -> Optional[str]:
    """Look up a Clerk user's primary email via the Admin API (cached per user)."""
    if user_id in _email_cache:
        return _email_cache[user_id]
    secret = os.getenv("CLERK_SECRET_KEY")
    if not secret:
        return None
    email: Optional[str] = None
    try:
        with httpx.Client(timeout=5.0, headers={"Authorization": f"Bearer {secret}"}) as client:
            r = client.get(f"https://api.clerk.com/v1/users/{user_id}")
            if r.status_code == 200:
                data = r.json()
                primary_id = data.get("primary_email_address_id")
                addresses = data.get("email_addresses", []) or []
                for addr in addresses:
                    if addr.get("id") == primary_id:
                        email = addr.get("email_address")
                        break
                if email is None and addresses:
                    email = addresses[0].get("email_address")
    except Exception:
        return None
    _email_cache[user_id] = email
    return email


def extract_actor(authorization: Optional[str]) -> Optional[dict]:
    """Verify a Clerk session JWT and return {"user_id", "email"}, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified.get("iss")
        if not issuer:
            return None
        expected = os.getenv("CLERK_ISSUER")
        if expected and issuer.rstrip("/") != expected.rstrip("/"):
            return None
        signing_key = _jwks_client_for(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except Exception:
        return None
    user_id = claims.get("sub")
    if not user_id:
        return None
    email = claims.get("email") or _resolve_email(user_id)
    return {"user_id": user_id, "email": email}


class ActorMiddleware:
    """Pure-ASGI middleware that sets ``current_actor`` for the request's duration.

    Implemented as pure ASGI (not Starlette ``BaseHTTPMiddleware``) so the
    contextvar set here propagates into the sync route handler — which Starlette
    runs in a threadpool that copies the current context.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        auth_value: Optional[str] = None
        for key, value in scope.get("headers", []):
            if key == b"authorization":
                auth_value = value.decode("latin-1")
                break
        actor = await run_in_threadpool(extract_actor, auth_value)
        token = current_actor.set(actor)
        try:
            await self.app(scope, receive, send)
        finally:
            current_actor.reset(token)
