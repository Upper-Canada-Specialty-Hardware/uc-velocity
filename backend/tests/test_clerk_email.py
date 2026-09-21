"""Functional tests for Clerk webhook authentication and route behavior."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import clerk_email as clerk_email_module
from clerk_email import MAX_BODY_BYTES, ClerkEmailMessage
from email_delivery import EmailSendError
from routes import clerk_email as clerk_email_route


_SECRET_BYTES = b"isolated-webhook-test-secret"
_SIGNING_SECRET = "whsec_" + base64.b64encode(_SECRET_BYTES).decode("ascii")


def _email_event(
    *,
    delivered_by_clerk: bool = False,
    html_body: str | None = "<p>Code: <strong>123456</strong></p>\n",
    text_body: str | None = "Code: 123456\n",
) -> dict[str, Any]:
    """Build one representative Clerk email event for route tests."""
    return {
        "type": "email.created",
        "data": {
            "object": "email",
            "id": "ema_test_123",
            "to_email_address": "person@example.com",
            "subject": "Your sign-in code ✓",
            "body": html_body,
            "body_plain": text_body,
            "delivered_by_clerk": delivered_by_clerk,
        },
    }


def _verification_event(otp_code: object = "012345") -> dict[str, Any]:
    """Build Clerk's confirmed verification-code metadata shape."""
    event = _email_event()
    email_data = event["data"]
    assert isinstance(email_data, dict)
    email_data["slug"] = "verification_code"
    email_data["data"] = {"otp_code": otp_code}
    return event


def _encode(payload: object) -> bytes:
    """Encode JSON deterministically while retaining non-ASCII body content."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _signed_headers(
    raw_body: bytes,
    *,
    timestamp: int | None = None,
    family: str = "svix",
) -> dict[str, str]:
    """Create valid Svix-compatible headers over exact request bytes."""
    message_id = "msg_test_123"
    timestamp_text = str(int(time.time()) if timestamp is None else timestamp)
    signed = f"{message_id}.{timestamp_text}.".encode("utf-8") + raw_body
    signature = base64.b64encode(
        hmac.new(_SECRET_BYTES, signed, hashlib.sha256).digest()
    ).decode("ascii")
    prefix = "svix" if family == "svix" else "webhook"
    return {
        f"{prefix}-id": message_id,
        f"{prefix}-timestamp": timestamp_text,
        f"{prefix}-signature": f"v1,{signature}",
        "content-type": "application/json",
    }


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    """Create an isolated app with no import of the real database module."""
    monkeypatch.setenv("CLERK_WEBHOOK_SIGNING_SECRET", _SIGNING_SECRET)
    app = FastAPI()
    app.include_router(clerk_email_route.router)

    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("family", ["svix", "standard"])
def test_valid_header_families_preserve_exact_clerk_message(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    family: str,
) -> None:
    """Relay exact decoded fields for either complete supported header family."""
    html_body = "<p>Line one</p>\n<p>Café &amp; code 123456</p>"
    text_body = "Line one\nCafé & code 123456\n"
    raw_body = _encode(
        _email_event(html_body=html_body, text_body=text_body)
    )
    captured: list[ClerkEmailMessage] = []

    def capture_delivery(message: ClerkEmailMessage) -> bool:
        """Capture the validated message instead of calling a provider."""
        captured.append(message)
        return True

    monkeypatch.setattr(clerk_email_route, "run_delivery", capture_delivery)
    response = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body, family=family),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "delivered"}
    assert captured == [
        ClerkEmailMessage(
            email_id="ema_test_123",
            to_email="person@example.com",
            subject="Your sign-in code ✓",
            html_body=html_body,
            text_body=text_body,
        )
    ]


def test_invalid_signature_is_rejected_before_delivery(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a mismatched HMAC without invoking delivery."""
    raw_body = _encode(_verification_event())
    headers = _signed_headers(raw_body)
    headers["svix-signature"] = "v1," + base64.b64encode(b"wrong").decode()

    def unexpected_delivery(message: ClerkEmailMessage) -> bool:
        """Fail if an unauthenticated request reaches delivery."""
        raise AssertionError("delivery must not run")

    monkeypatch.setattr(
        clerk_email_route, "run_delivery", unexpected_delivery
    )
    monkeypatch.setattr(
        clerk_email_module,
        "render_verification_code_email",
        lambda code: pytest.fail("rendering must not run"),
    )
    response = client.post(
        "/webhooks/clerk/email", content=raw_body, headers=headers
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid webhook signature"}


def test_signed_verification_event_renders_exact_leading_zero_code(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render local HTML and text after auth while preserving leading zeroes."""
    event = _verification_event("001204")
    email_data = event["data"]
    assert isinstance(email_data, dict)
    expected_subject = str(email_data["subject"])
    raw_body = _encode(event)
    captured: list[ClerkEmailMessage] = []

    def capture_delivery(message: ClerkEmailMessage) -> bool:
        """Capture the fully prepared message instead of sending it."""
        captured.append(message)
        return True

    monkeypatch.setattr(clerk_email_route, "run_delivery", capture_delivery)
    response = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "delivered"}
    assert len(captured) == 1
    assert captured[0].subject == expected_subject
    assert "<strong>001204</strong>" in (captured[0].html_body or "")
    assert "verification code is 001204" in (captured[0].text_body or "")


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {"otp_code": ""},
        {"otp_code": "   "},
        {"otp_code": 123456},
        {"otp_code": "1" * 129},
    ],
)
def test_invalid_verification_metadata_is_rejected_without_delivery(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    metadata: object,
) -> None:
    """Reject missing, blank, non-string, or oversized Clerk code metadata."""
    event = _verification_event()
    email_data = event["data"]
    assert isinstance(email_data, dict)
    email_data["data"] = metadata
    raw_body = _encode(event)
    monkeypatch.setattr(
        clerk_email_route,
        "run_delivery",
        lambda message: pytest.fail("delivery must not run"),
    )

    response = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "invalid webhook payload"}


def test_other_email_slug_keeps_clerk_bodies_unchanged(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep non-verification templates on the existing passthrough path."""
    html_body = "<p>Clerk magic-link body {{ untouched }}</p>"
    text_body = "Clerk magic-link body {{ untouched }}"
    event = _email_event(html_body=html_body, text_body=text_body)
    email_data = event["data"]
    assert isinstance(email_data, dict)
    email_data["slug"] = "magic_link"
    email_data["data"] = {"otp_code": "must-not-render"}
    raw_body = _encode(event)
    captured: list[ClerkEmailMessage] = []
    monkeypatch.setattr(
        clerk_email_route,
        "run_delivery",
        lambda message: captured.append(message) or True,
    )

    response = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 200
    assert captured[0].html_body == html_body
    assert captured[0].text_body == text_body


def test_stale_signature_is_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject an otherwise valid signature outside the replay window."""
    raw_body = _encode(_email_event())
    headers = _signed_headers(raw_body, timestamp=int(time.time()) - 301)
    monkeypatch.setattr(
        clerk_email_route,
        "run_delivery",
        lambda message: pytest.fail("delivery must not run"),
    )

    response = client.post(
        "/webhooks/clerk/email", content=raw_body, headers=headers
    )

    assert response.status_code == 401


def test_mixed_signature_families_are_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject ambiguous headers even when one family is otherwise complete."""
    raw_body = _encode(_email_event())
    headers = _signed_headers(raw_body)
    # One Standard Webhooks header makes the two families ambiguous.
    headers["webhook-id"] = "msg_other"
    monkeypatch.setattr(
        clerk_email_route,
        "run_delivery",
        lambda message: pytest.fail("delivery must not run"),
    )

    response = client.post(
        "/webhooks/clerk/email", content=raw_body, headers=headers
    )

    assert response.status_code == 401


def test_oversized_body_is_rejected_before_signature_work(
    client: TestClient,
) -> None:
    """Stop buffering an unauthenticated body after the fixed maximum."""
    response = client.post(
        "/webhooks/clerk/email",
        content=b"x" * (MAX_BODY_BYTES + 1),
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "webhook body is too large"}


@pytest.mark.parametrize(
    "raw_body",
    [
        b"{not-json",
        _encode(
            {
                "type": "email.created",
                "data": {
                    "object": "email",
                    "id": "ema_test_123",
                    "to_email_address": "person@example.com",
                    "subject": "Subject",
                    "body": "Body",
                },
            }
        ),
    ],
)
def test_malformed_selected_payload_is_rejected(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    raw_body: bytes,
) -> None:
    """Reject malformed JSON and email events without explicit external delivery."""
    monkeypatch.setattr(
        clerk_email_route,
        "run_delivery",
        lambda message: pytest.fail("delivery must not run"),
    )

    response = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "session.created", "data": {}},
        _email_event(delivered_by_clerk=True),
    ],
)
def test_signed_events_outside_external_delivery_are_ignored(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    """Acknowledge unrelated or Clerk-delivered events without another send."""
    raw_body = _encode(payload)
    monkeypatch.setattr(
        clerk_email_route,
        "run_delivery",
        lambda message: pytest.fail("delivery must not run"),
    )

    response = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}


def test_provider_failure_remains_retryable(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a non-success first and deliver a later Clerk retry."""
    raw_body = _encode(_email_event())
    attempts = 0

    def fail_then_succeed(message: ClerkEmailMessage) -> bool:
        """Model one provider failure followed by successful acceptance."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise EmailSendError("sanitized provider failure")
        return True

    monkeypatch.setattr(
        clerk_email_route, "run_delivery", fail_then_succeed
    )
    first = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )
    second = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert first.status_code == 502
    assert first.json() == {"detail": "email delivery failed"}
    assert second.status_code == 200
    assert second.json() == {"status": "delivered"}
    assert attempts == 2


def test_existing_receipt_is_reported_as_duplicate(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acknowledge a durable duplicate without claiming a new delivery."""
    raw_body = _encode(_email_event())
    monkeypatch.setattr(
        clerk_email_route, "run_delivery", lambda message: False
    )

    response = client.post(
        "/webhooks/clerk/email",
        content=raw_body,
        headers=_signed_headers(raw_body),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "duplicate"}
