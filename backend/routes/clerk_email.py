"""Receive authenticated Clerk email events and relay selected messages."""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from clerk_email import (
    WebhookAuthenticationError,
    WebhookBodyTooLarge,
    WebhookPayloadError,
    parse_event,
    read_bounded_body,
    signature_headers,
    verify_signature,
)
from email_delivery import (
    EmailDeliveryBusy,
    EmailDeliveryConfigurationError,
    EmailDeliveryPersistenceError,
    EmailSendError,
    run_delivery,
)


router = APIRouter(prefix="/webhooks/clerk", tags=["clerk-webhooks"])


@router.post("/email")
async def receive_clerk_email(
    request: Request,
) -> dict[str, str]:
    """Authenticate a Clerk event and relay its prepared email through SMTP2GO.

    Args:
        request: Incoming request containing exact signed webhook bytes.
    Returns:
        A status indicating delivery, a prior duplicate, or an ignored event.

    Raises:
        HTTPException: For oversized, unauthenticated, invalid, unconfigured,
            busy, provider-rejected, or persistence-failed requests.
    """
    try:
        raw_body = await read_bounded_body(request)
    except WebhookBodyTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    # Missing signing configuration fails this request without breaking startup.
    signing_secret = (os.getenv("CLERK_WEBHOOK_SIGNING_SECRET") or "").strip()
    if not signing_secret:
        raise HTTPException(status_code=503, detail="webhook is not configured")
    try:
        message_id, timestamp, signatures = signature_headers(request.headers)
        verify_signature(
            raw_body,
            message_id,
            timestamp,
            signatures,
            signing_secret,
        )
    except WebhookAuthenticationError as exc:
        raise HTTPException(status_code=401, detail="invalid webhook signature") from exc
    try:
        message = parse_event(raw_body)
    except WebhookPayloadError as exc:
        raise HTTPException(status_code=400, detail="invalid webhook payload") from exc
    if message is None:
        return {"status": "ignored"}

    try:
        # Keep synchronous SQLAlchemy, psycopg2, and HTTP work off the event loop.
        sent = await run_in_threadpool(run_delivery, message)
    except EmailDeliveryConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="email delivery is not configured"
        ) from exc
    except EmailDeliveryBusy as exc:
        # Ask Clerk to retry after the concurrent transaction finishes.
        raise HTTPException(status_code=503, detail="email delivery is busy") from exc
    except EmailSendError as exc:
        # A non-success keeps Clerk's webhook retry path active.
        raise HTTPException(status_code=502, detail="email delivery failed") from exc
    except EmailDeliveryPersistenceError as exc:
        raise HTTPException(status_code=503, detail="email delivery failed") from exc
    return {"status": "delivered" if sent else "duplicate"}
