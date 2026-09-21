"""Authenticate and validate Clerk email webhook requests.

Clerk remains responsible for generating and verifying sign-in codes. This
module authenticates Clerk's signed ``email.created`` event, prepares the local
verification-code bodies, and preserves other Clerk messages unchanged.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Request

from email_templates import render_verification_code_email


# Bound unauthenticated input before JSON parsing or signature work.
MAX_BODY_BYTES = 256 * 1024
# Accept normal delivery delay while rejecting replayed webhook requests.
MAX_SIGNATURE_AGE_SECONDS = 300


class WebhookAuthenticationError(ValueError):
    """Raised when a webhook signature cannot be authenticated."""


class WebhookPayloadError(ValueError):
    """Raised when a signed email event has an invalid or unsafe shape."""


class WebhookBodyTooLarge(ValueError):
    """Raised when a streamed webhook body exceeds the fixed request limit."""


@dataclass(frozen=True, repr=False)
class ClerkEmailMessage:
    """Hold one validated Clerk message without exposing its code separately."""

    email_id: str
    to_email: str
    subject: str
    html_body: str | None
    text_body: str | None


async def read_bounded_body(request: Request) -> bytes:
    """Read the signed request without buffering more than 256 KiB.

    Args:
        request: Incoming FastAPI request.

    Returns:
        The complete raw bytes used for signature verification and parsing.

    Raises:
        WebhookBodyTooLarge: If the streamed request exceeds the fixed limit.
    """
    body = bytearray()
    async for chunk in request.stream():
        # Enforce the limit even when Content-Length is absent or untrusted.
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise WebhookBodyTooLarge("webhook body is too large")
        # Retain the exact bytes covered by Clerk's signature.
        body.extend(chunk)
    return bytes(body)


def signature_headers(headers: Mapping[str, str]) -> tuple[str, str, str]:
    """Select one complete supported webhook signature header family.

    Args:
        headers: Case-insensitive request headers supplied by Starlette.

    Returns:
        The message id, timestamp, and versioned signature list.

    Raises:
        WebhookAuthenticationError: If neither family is complete or the two
            families are mixed.
    """
    # Clerk can use the older Svix names or Standard Webhooks names.
    svix = (
        (headers.get("svix-id") or "").strip(),
        (headers.get("svix-timestamp") or "").strip(),
        (headers.get("svix-signature") or "").strip(),
    )
    standard = (
        (headers.get("webhook-id") or "").strip(),
        (headers.get("webhook-timestamp") or "").strip(),
        (headers.get("webhook-signature") or "").strip(),
    )
    # Accept only a complete, unambiguous Svix family.
    if all(svix) and not any(standard):
        return svix
    # Accept only a complete, unambiguous Standard Webhooks family.
    if all(standard) and not any(svix):
        return standard
    raise WebhookAuthenticationError("invalid webhook signature")


def verify_signature(
    raw_body: bytes,
    message_id: str,
    timestamp_text: str,
    signatures: str,
    signing_secret: str,
    *,
    now: float | None = None,
) -> None:
    """Verify a Clerk/Svix HMAC over the exact request bytes.

    Args:
        raw_body: Unparsed request body.
        message_id: Signed webhook message id.
        timestamp_text: Signed Unix timestamp header.
        signatures: Space-separated versioned signatures.
        signing_secret: Clerk's ``whsec_`` endpoint secret.
        now: Optional wall clock used by deterministic tests.

    Raises:
        WebhookAuthenticationError: If any input is malformed, stale, too far
            in the future, or lacks a matching ``v1`` signature.
    """
    # Require every value before performing cryptographic work.
    if not message_id or not timestamp_text or not signatures:
        raise WebhookAuthenticationError("invalid webhook signature")
    # Refuse unrelated secret formats to prevent silent misconfiguration.
    if not signing_secret.startswith("whsec_"):
        raise WebhookAuthenticationError("invalid webhook signature")
    # Bound integer parsing and require Clerk's ASCII Unix-seconds format.
    if (
        not timestamp_text.isascii()
        or not timestamp_text.isdecimal()
        or len(timestamp_text) > 10
    ):
        raise WebhookAuthenticationError("invalid webhook signature")
    timestamp = int(timestamp_text)
    current_time = int(time.time() if now is None else now)
    # Reject old replays and implausible future deliveries symmetrically.
    if abs(current_time - timestamp) > MAX_SIGNATURE_AGE_SECONDS:
        raise WebhookAuthenticationError("invalid webhook signature")
    try:
        # Clerk encodes endpoint secrets after the ``whsec_`` prefix.
        secret = base64.b64decode(
            signing_secret.removeprefix("whsec_"), validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise WebhookAuthenticationError("invalid webhook signature") from exc
    if not secret:
        raise WebhookAuthenticationError("invalid webhook signature")
    # Preserve the protocol's id.timestamp.raw-body signing format.
    signed = f"{message_id}.{timestamp_text}.".encode("utf-8") + raw_body
    expected = hmac.new(secret, signed, hashlib.sha256).digest()
    for candidate in signatures.split():
        # Rotation can provide several space-separated versioned signatures.
        version, separator, encoded = candidate.partition(",")
        if version != "v1" or separator != "," or not encoded:
            continue
        try:
            supplied = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            # A later rotated signature may still authenticate this request.
            continue
        if hmac.compare_digest(expected, supplied):
            return
    raise WebhookAuthenticationError("invalid webhook signature")


def parse_event(raw_body: bytes) -> ClerkEmailMessage | None:
    """Parse a signed Clerk event and validate messages selected for relay.

    Args:
        raw_body: Already authenticated request bytes.

    Returns:
        A validated message, or ``None`` for unrelated events and messages
        already delivered by Clerk.

    Raises:
        WebhookPayloadError: If JSON or a selected email event is invalid.
    """
    try:
        event: Any = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookPayloadError("invalid webhook payload") from exc
    if not isinstance(event, dict):
        raise WebhookPayloadError("invalid webhook payload")
    # Signed event types outside this one endpoint's responsibility are safe.
    if event.get("type") != "email.created":
        return None
    data = event.get("data")
    if not isinstance(data, dict) or data.get("object") != "email":
        raise WebhookPayloadError("invalid webhook payload")
    delivered_by_clerk = data.get("delivered_by_clerk")
    # Avoid duplicating any message Clerk has already sent itself.
    if delivered_by_clerk is True:
        return None
    # Require an explicit false value before taking delivery ownership.
    if delivered_by_clerk is not False:
        raise WebhookPayloadError("invalid webhook payload")

    email_id = _bounded_text(data, "id", 255)
    recipient = _bounded_text(data, "to_email_address", 320)
    subject = _bounded_text(data, "subject", 998)
    # Prevent recipient-list or header injection into the provider payload.
    if any(character in recipient for character in "\r\n,;"):
        raise WebhookPayloadError("invalid webhook payload")
    if recipient.count("@") != 1 or any(
        character.isspace() for character in recipient
    ):
        raise WebhookPayloadError("invalid webhook payload")
    local, domain = recipient.rsplit("@", 1)
    if not local or not domain:
        raise WebhookPayloadError("invalid webhook payload")
    if "\r" in subject or "\n" in subject:
        raise WebhookPayloadError("invalid webhook payload")

    if data.get("slug") == "verification_code":
        # Clerk generates the code; the local template controls only presentation.
        template_data = data.get("data")
        if not isinstance(template_data, dict):
            raise WebhookPayloadError("invalid webhook payload")
        otp_code = _bounded_text(template_data, "otp_code", 128)
        # Render only after signature verification and metadata validation.
        html_body, text_body = render_verification_code_email(otp_code)
    else:
        # Preserve bodies for every other Clerk email template unchanged.
        html_body = _optional_body(data, "body")
        text_body = _optional_body(data, "body_plain")
    if not any(
        body is not None and body.strip()
        for body in (html_body, text_body)
    ):
        raise WebhookPayloadError("invalid webhook payload")
    return ClerkEmailMessage(
        email_id=email_id,
        to_email=recipient,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def _bounded_text(data: dict[str, Any], key: str, limit: int) -> str:
    """Return one required nonblank string within its protocol bound."""
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise WebhookPayloadError("invalid webhook payload")
    return value


def _optional_body(data: dict[str, Any], key: str) -> str | None:
    """Return one optional body while rejecting non-string values."""
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise WebhookPayloadError("invalid webhook payload")
    return value
