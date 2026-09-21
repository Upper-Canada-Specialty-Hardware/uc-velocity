"""Tests for local verification email rendering and provider handoff."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from clerk_email import parse_event
from email_delivery import EmailDeliveryConfig, send_via_smtp2go
from email_templates import render_verification_code_email


def _verification_body(code: str) -> bytes:
    """Encode one authenticated-shape event for parser and transport tests."""
    return json.dumps(
        {
            "type": "email.created",
            "data": {
                "object": "email",
                "id": "ema_template_test",
                "to_email_address": "person@example.com",
                "subject": "Your verification code",
                "body": "Clerk body is intentionally replaced",
                "body_plain": "Clerk text is intentionally replaced",
                "delivered_by_clerk": False,
                "slug": "verification_code",
                "data": {"otp_code": code},
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def test_renderer_escapes_html_without_executing_webhook_content() -> None:
    """Treat the signed code as data in HTML and literal text in plain output."""
    code = "<em>{{ 7 * 7 }}</em>"

    html_body, text_body = render_verification_code_email(code)

    assert "&lt;em&gt;{{ 7 * 7 }}&lt;/em&gt;" in html_body
    assert "<em>" not in html_body
    assert "49" not in html_body
    assert code in text_body
    assert "49" not in text_body


def test_renderer_is_independent_of_current_working_directory(
    monkeypatch,
) -> None:
    """Load module-relative templates when Railway starts from another CWD."""
    # The tests directory is a stable non-template working directory.
    monkeypatch.chdir(Path(__file__).parent)

    html_body, text_body = render_verification_code_email("000042")

    assert "<strong>000042</strong>" in html_body
    assert "verification code is 000042" in text_body


def test_rendered_bodies_reach_mocked_smtp2go_transport_unchanged() -> None:
    """Carry the local rendering through the existing provider JSON boundary."""
    captured_payloads: list[dict[str, object]] = []

    def accept(request: httpx.Request) -> httpx.Response:
        """Capture the provider request and report one accepted message."""
        captured_payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"data": {"succeeded": 1, "failed": 0}},
        )

    message = parse_event(_verification_body("007305"))
    assert message is not None
    with httpx.Client(transport=httpx.MockTransport(accept)) as client:
        send_via_smtp2go(
            message,
            EmailDeliveryConfig(
                api_key="test-api-key",
                sender="UC Velocity <HR@s2gms.com>",
            ),
            client=client,
        )

    assert len(captured_payloads) == 1
    assert "<strong>007305</strong>" in str(captured_payloads[0]["html_body"])
    assert "verification code is 007305" in str(
        captured_payloads[0]["text_body"]
    )
