"""Unit tests for SMTP2GO transport and transactional delivery receipts."""
from __future__ import annotations

import logging
import io
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from clerk_email import ClerkEmailMessage
from email_delivery import (
    EmailDeliveryBusy,
    EmailDeliveryConfig,
    EmailDeliveryConfigurationError,
    EmailDeliveryPersistenceError,
    EmailSendError,
    MAILER_TIMEOUT_SECONDS,
    MailerConfig,
    SMTP2GO_ENDPOINT,
    SMTP2GO_TIMEOUT_SECONDS,
    clerk_email_lock_key,
    deliver_clerk_email,
    is_internal_recipient,
    load_email_delivery_config,
    load_mailer_config,
    logger as email_delivery_logger,
    mailer_idempotency_key,
    send_via_mailer,
    send_via_smtp2go,
)


@dataclass
class _Store:
    """Hold fake durable receipts shared by isolated test sessions."""

    receipts: set[str] = field(default_factory=set)
    lock_available: bool = True
    insert_count: int = 0


class _Result:
    """Expose SQLAlchemy's scalar result surface for controlled values."""

    def __init__(self, value: object) -> None:
        """Store the scalar value returned by one fake statement."""
        self.value = value

    def scalar_one(self) -> object:
        """Return the fake statement's single scalar value."""
        return self.value


class _Transaction:
    """Snapshot fake storage so exceptions model transaction rollback."""

    def __init__(self, session: "_Session") -> None:
        """Bind the transaction to its fake session."""
        self.session = session
        self.before: set[str] = set()

    def __enter__(self) -> "_Transaction":
        """Capture receipt state before transactional work starts."""
        self.before = set(self.session.store.receipts)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> bool:
        """Roll back exceptions and optionally model a failed commit."""
        if exc_type is not None or self.session.fail_commit:
            self.session.store.receipts = self.before
        if exc_type is None and self.session.fail_commit:
            raise OperationalError("COMMIT", {}, RuntimeError("commit failed"))
        return False


class _Session:
    """Model only the PostgreSQL statements used by delivery coordination."""

    def __init__(self, store: _Store, *, fail_commit: bool = False) -> None:
        """Create a controlled transaction session over shared fake storage."""
        self.store = store
        self.fail_commit = fail_commit

    def begin(self) -> _Transaction:
        """Open a rollback-capable fake transaction context."""
        return _Transaction(self)

    def execute(
        self,
        statement: object,
        parameters: dict[str, object] | None = None,
    ) -> _Result:
        """Execute the small allowlisted statement set used by the service."""
        sql = str(statement)
        values = parameters or {}
        if "set_config" in sql:
            return _Result("5000ms")
        if "pg_try_advisory_xact_lock" in sql:
            return _Result(self.store.lock_available)
        if "SELECT EXISTS" in sql:
            return _Result(str(values["email_id"]) in self.store.receipts)
        if "INSERT INTO clerk_email_deliveries" in sql:
            self.store.receipts.add(str(values["email_id"]))
            self.store.insert_count += 1
            return _Result(None)
        raise AssertionError(f"unexpected SQL in test: {sql}")


class _RecordingClient:
    """Capture provider payloads and return controlled HTTP outcomes."""

    def __init__(self, outcomes: list[httpx.Response | httpx.HTTPError]) -> None:
        """Queue HTTP responses or exceptions for successive calls."""
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Record one call and return its queued provider outcome."""
        call: dict[str, Any] = {"url": url, "json": json, "timeout": timeout}
        if headers is not None:
            # Only mailer calls send headers; SMTP2GO call records stay unchanged.
            call["headers"] = headers
        self.calls.append(call)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, httpx.HTTPError):
            raise outcome
        return outcome


def _message(email_id: str = "ema_delivery_123") -> ClerkEmailMessage:
    """Return one message with exact HTML and plain-text bodies."""
    return ClerkEmailMessage(
        email_id=email_id,
        to_email="person@example.com",
        subject="Clerk subject ✓",
        html_body="<p>Code <strong>123456</strong></p>\n",
        text_body="Code 123456\n",
    )


def _config() -> EmailDeliveryConfig:
    """Return inert provider settings used only by mocked requests."""
    return EmailDeliveryConfig(
        api_key="test-api-key",
        sender="UC Velocity <HR@s2gms.com>",
    )


def _accepted_response() -> httpx.Response:
    """Return SMTP2GO's exact successful acceptance-count shape."""
    return httpx.Response(200, json={"data": {"succeeded": 1, "failed": 0}})


@contextmanager
def _capture_provider_logs(
    caplog: pytest.LogCaptureFixture,
) -> Generator[None, None, None]:
    """Attach pytest's capture handler to the non-propagating module logger."""
    email_delivery_logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="email_delivery"):
            yield
    finally:
        email_delivery_logger.removeHandler(caplog.handler)


def test_transport_sends_exact_clerk_content_and_configured_sender() -> None:
    """Build the SMTP2GO request without rewriting either Clerk body."""
    client = _RecordingClient([_accepted_response()])

    send_via_smtp2go(_message(), _config(), client=client)

    assert client.calls == [
        {
            "url": SMTP2GO_ENDPOINT,
            "json": {
                "api_key": "test-api-key",
                "sender": "UC Velocity <HR@s2gms.com>",
                "to": ["person@example.com"],
                "subject": "Clerk subject ✓",
                "html_body": "<p>Code <strong>123456</strong></p>\n",
                "text_body": "Code 123456\n",
            },
            "timeout": SMTP2GO_TIMEOUT_SECONDS,
        }
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"data": {"succeeded": 0, "failed": 1}}),
        httpx.Response(200, json={"data": {"succeeded": 0, "failed": 1}}),
        httpx.Response(200, json={"data": {"succeeded": True, "failed": 0}}),
        httpx.Response(200, content=b"not-json"),
    ],
)
def test_transport_rejects_http_count_and_shape_failures(
    response: httpx.Response,
) -> None:
    """Accept only a 2xx response with exact integer acceptance counts."""
    client = _RecordingClient([response])

    with pytest.raises(EmailSendError):
        send_via_smtp2go(_message(), _config(), client=client)


def test_transport_logs_only_allowlisted_accepted_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Expose safe success correlation without message or credential content."""
    response = httpx.Response(
        200,
        json={
            "request_id": "550e8400-e29b-41d4-a716-446655440000",
            "data": {
                "email_id": "smtp2go-email_123",
                "succeeded": 1,
                "failed": 0,
            },
        },
    )
    client = _RecordingClient([response])

    with _capture_provider_logs(caplog):
        send_via_smtp2go(_message(), _config(), client=client)

    log_text = caplog.text
    assert "outcome=accepted" in log_text
    assert "http_status=200" in log_text
    assert "succeeded=1" in log_text
    assert "failed=0" in log_text
    assert "request_id=550e8400-e29b-41d4-a716-446655440000" in log_text
    assert "email_id=smtp2go-email_123" in log_text
    assert "person@example.com" not in log_text
    assert "123456" not in log_text
    assert "test-api-key" not in log_text


def test_transport_logs_sanitized_http_rejection_without_raw_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Retain safe rejection fields while dropping provider error text and body."""
    response = httpx.Response(
        403,
        json={
            "request_id": "request-safe_123",
            "data": {
                "succeeded": 0,
                "failed": 1,
                "email_id": "unsafe\nemail-id",
                "error_code": "E_ApiResponseCodes.ENDPOINT_PERMISSION_DENIED",
                "failures": [
                    {
                        "email": "person@example.com",
                        "error": "secret code 123456 and raw provider detail",
                    }
                ],
            },
            "error": "another raw provider error",
        },
    )
    client = _RecordingClient([response])

    with _capture_provider_logs(caplog):
        with pytest.raises(EmailSendError):
            send_via_smtp2go(_message(), _config(), client=client)

    log_text = caplog.text
    assert "outcome=http_rejected" in log_text
    assert "http_status=403" in log_text
    assert "succeeded=0" in log_text
    assert "failed=1" in log_text
    assert "request_id=request-safe_123" in log_text
    assert "error_code=E_ApiResponseCodes.ENDPOINT_PERMISSION_DENIED" in log_text
    assert "unsafe" not in log_text
    assert "person@example.com" not in log_text
    assert "123456" not in log_text
    assert "raw provider" not in log_text


def test_transport_logs_invalid_response_without_raw_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Classify malformed provider data without echoing its response body."""
    raw_body = b"not-json person@example.com secret-code-123456"
    client = _RecordingClient([httpx.Response(200, content=raw_body)])

    with _capture_provider_logs(caplog):
        with pytest.raises(EmailSendError):
            send_via_smtp2go(_message(), _config(), client=client)

    log_text = caplog.text
    assert "outcome=invalid_response" in log_text
    assert "person@example.com" not in log_text
    assert "123456" not in log_text
    assert "not-json" not in log_text


def test_transport_logs_network_error_without_exception_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Classify network failure without logging the exception representation."""
    request = httpx.Request("POST", SMTP2GO_ENDPOINT)
    network_error = httpx.ConnectError(
        "person@example.com secret-code-123456 test-api-key",
        request=request,
    )
    client = _RecordingClient([network_error])

    with _capture_provider_logs(caplog):
        with pytest.raises(EmailSendError):
            send_via_smtp2go(_message(), _config(), client=client)

    log_text = caplog.text
    assert "outcome=network_error" in log_text
    assert "person@example.com" not in log_text
    assert "123456" not in log_text
    assert "test-api-key" not in log_text


def test_transport_bounds_untrusted_diagnostic_counts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Replace out-of-range provider integers with safe placeholders in logs."""
    response = httpx.Response(
        200,
        json={
            "data": {
                "succeeded": 999999,
                "failed": -999999,
            }
        },
    )
    client = _RecordingClient([response])

    with _capture_provider_logs(caplog):
        with pytest.raises(EmailSendError):
            send_via_smtp2go(_message(), _config(), client=client)

    assert "outcome=invalid_response" in caplog.text
    assert "succeeded=- failed=-" in caplog.text
    assert "999999" not in caplog.text


def test_module_handler_emits_accepted_info_without_root_handlers() -> None:
    """Keep successful provider diagnostics visible without global logging setup."""
    root_logger = logging.getLogger()
    original_root_handlers = list(root_logger.handlers)
    module_handler = email_delivery_logger.handlers[0]
    original_stream = module_handler.stream
    captured_stream = io.StringIO()
    try:
        root_logger.handlers.clear()
        module_handler.setStream(captured_stream)
        send_via_smtp2go(
            _message(),
            _config(),
            client=_RecordingClient([_accepted_response()]),
        )
    finally:
        module_handler.setStream(original_stream)
        root_logger.handlers[:] = original_root_handlers

    assert "outcome=accepted" in captured_stream.getvalue()
    assert "succeeded=1 failed=0" in captured_stream.getvalue()


def test_missing_provider_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep startup import-safe while refusing an unconfigured send."""
    monkeypatch.delenv("SMTP2GO_API_KEY", raising=False)
    monkeypatch.delenv("SMTP2GO_SENDER", raising=False)

    with pytest.raises(EmailDeliveryConfigurationError):
        load_email_delivery_config()


def test_receipt_commits_after_acceptance_and_suppresses_duplicate() -> None:
    """Persist one receipt and avoid a second provider call on Clerk retry."""
    store = _Store()
    client = _RecordingClient([_accepted_response()])

    first = deliver_clerk_email(
        _Session(store), _message(), config=_config(), client=client
    )
    second = deliver_clerk_email(
        _Session(store), _message(), config=_config(), client=client
    )

    assert first is True
    assert second is False
    assert store.receipts == {"ema_delivery_123"}
    assert store.insert_count == 1
    assert len(client.calls) == 1


def test_provider_failure_rolls_back_and_later_retry_can_deliver() -> None:
    """Leave no receipt after rejection so a later webhook can retry safely."""
    store = _Store()
    failed = httpx.Response(
        200, json={"data": {"succeeded": 0, "failed": 1}}
    )
    client = _RecordingClient([failed, _accepted_response()])

    with pytest.raises(EmailSendError):
        deliver_clerk_email(
            _Session(store), _message(), config=_config(), client=client
        )
    assert store.receipts == set()

    assert (
        deliver_clerk_email(
            _Session(store), _message(), config=_config(), client=client
        )
        is True
    )
    assert store.receipts == {"ema_delivery_123"}
    assert len(client.calls) == 2


def test_commit_failure_is_not_acknowledged_as_delivery() -> None:
    """Raise after provider acceptance when the durable receipt cannot commit."""
    store = _Store()
    client = _RecordingClient([_accepted_response()])

    with pytest.raises(EmailDeliveryPersistenceError):
        deliver_clerk_email(
            _Session(store, fail_commit=True),
            _message(),
            config=_config(),
            client=client,
        )

    assert store.receipts == set()
    assert len(client.calls) == 1


def test_contended_advisory_lock_is_retryable_without_provider_call() -> None:
    """Refuse concurrent same-email work before checking or sending content."""
    store = _Store(lock_available=False)
    client = _RecordingClient([])

    with pytest.raises(EmailDeliveryBusy):
        deliver_clerk_email(
            _Session(store), _message(), config=_config(), client=client
        )

    assert store.receipts == set()
    assert client.calls == []


def test_lock_key_is_stable_namespaced_and_signed_64_bit() -> None:
    """Generate stable lock identities suitable for all Gunicorn workers."""
    first = clerk_email_lock_key("ema_delivery_123")
    second = clerk_email_lock_key("ema_delivery_123")
    other = clerk_email_lock_key("ema_delivery_124")

    assert first == second
    assert first != other
    assert -(2**63) <= first < 2**63


_POSTGRES_TEST_IDS = (
    "test_clerk_email_rollback",
    "test_clerk_email_concurrent",
)


def _postgres_test_database_url() -> str:
    """Select only an explicitly safe test database URL or skip integration."""
    database_url = (os.getenv("CLERK_EMAIL_TEST_DATABASE_URL") or "").strip()
    if not database_url and os.getenv("GITHUB_ACTIONS") == "true":
        # CI provisions ucvelocity_test and migrates it before pytest runs.
        database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        pytest.skip("no controlled Clerk email PostgreSQL test database")
    database_name = make_url(database_url).database or ""
    if not database_name.lower().endswith("_test"):
        pytest.fail("Clerk email integration tests require a *_test database")
    return database_url


@pytest.fixture
def postgres_session_factory(
) -> Generator[sessionmaker[Session], None, None]:
    """Open a controlled migrated test database and clean exact test receipts."""
    engine = create_engine(
        _postgres_test_database_url(),
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=0,
        connect_args={"connect_timeout": 5},
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    cleanup = text(
        "DELETE FROM clerk_email_deliveries "
        "WHERE clerk_email_id IN (:email_id_1, :email_id_2)"
    )
    cleanup_values = {
        "email_id_1": _POSTGRES_TEST_IDS[0],
        "email_id_2": _POSTGRES_TEST_IDS[1],
    }
    try:
        # Remove only this test module's known ids before and after execution.
        with engine.begin() as connection:
            connection.execute(cleanup, cleanup_values)
        yield factory
    finally:
        with engine.begin() as connection:
            connection.execute(cleanup, cleanup_values)
        engine.dispose()


def _receipt_count(
    factory: sessionmaker[Session], email_id: str
) -> int:
    """Count one controlled test receipt without reading message content."""
    with factory() as db:
        return int(
            db.execute(
                text(
                    "SELECT count(*) FROM clerk_email_deliveries "
                    "WHERE clerk_email_id = :email_id"
                ),
                {"email_id": email_id},
            ).scalar_one()
        )


def test_postgres_receipt_rolls_back_then_persists_on_retry(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    """Prove provider failure rollback and retry persistence in PostgreSQL."""
    email_id = "test_clerk_email_rollback"
    message = _message(email_id)
    failed = httpx.Response(
        200, json={"data": {"succeeded": 0, "failed": 1}}
    )
    client = _RecordingClient([failed, _accepted_response()])

    with postgres_session_factory() as db:
        with pytest.raises(EmailSendError):
            deliver_clerk_email(db, message, config=_config(), client=client)
    assert _receipt_count(postgres_session_factory, email_id) == 0

    with postgres_session_factory() as db:
        assert deliver_clerk_email(
            db, message, config=_config(), client=client
        ) is True
    assert _receipt_count(postgres_session_factory, email_id) == 1

    # The committed receipt suppresses the same provider message on retry.
    with postgres_session_factory() as db:
        assert deliver_clerk_email(
            db, message, config=_config(), client=client
        ) is False
    assert len(client.calls) == 2


class _BlockingClient:
    """Hold one accepted provider call while a second DB session contends."""

    def __init__(self) -> None:
        """Create coordination events for the two delivery threads."""
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        """Signal lock ownership and wait briefly before provider acceptance."""
        self.calls += 1
        self.started.set()
        if not self.release.wait(timeout=5):
            raise httpx.ReadTimeout("controlled provider wait expired")
        return _accepted_response()


def test_postgres_advisory_lock_serializes_same_email(
    postgres_session_factory: sessionmaker[Session],
) -> None:
    """Prove a second PostgreSQL session cannot send the same email concurrently."""
    email_id = "test_clerk_email_concurrent"
    message = _message(email_id)
    blocking_client = _BlockingClient()
    first_results: list[bool] = []
    first_errors: list[BaseException] = []

    def first_delivery() -> None:
        """Hold the advisory lock while the main thread tests contention."""
        try:
            with postgres_session_factory() as db:
                first_results.append(
                    deliver_clerk_email(
                        db,
                        message,
                        config=_config(),
                        client=blocking_client,
                    )
                )
        except BaseException as exc:
            first_errors.append(exc)

    worker = threading.Thread(target=first_delivery, daemon=True)
    worker.start()
    assert blocking_client.started.wait(timeout=5)
    try:
        with postgres_session_factory() as db:
            with pytest.raises(EmailDeliveryBusy):
                deliver_clerk_email(
                    db,
                    message,
                    config=_config(),
                    client=_RecordingClient([]),
                )
    finally:
        blocking_client.release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert first_errors == []
    assert first_results == [True]
    assert blocking_client.calls == 1
    assert _receipt_count(postgres_session_factory, email_id) == 1


# --- Internal mailer (Microsoft 365 HVE) for ucsh.com recipients ---


def _mailer_config() -> MailerConfig:
    """Return inert internal mailer settings used only by mocked requests."""
    return MailerConfig(url="https://mailer.test", key="ucshm_velocity_test")


def _internal_message(to_email: str = "staff@ucsh.com") -> ClerkEmailMessage:
    """Return one message addressed to an internal recipient."""
    return ClerkEmailMessage(
        email_id="ema_internal_1",
        to_email=to_email,
        subject="Your code",
        html_body="<p>Code <strong>123456</strong></p>\n",
        text_body="Code 123456\n",
    )


def _mailer_sent() -> httpx.Response:
    """Return the mailer's success shape for one recipient."""
    return httpx.Response(
        200,
        json={"status": "sent", "recipients": 1, "messages": [{"recipients": 1}]},
    )


def test_mailer_request_carries_key_idempotency_and_bodies() -> None:
    """Send the same subject and bodies to the mailer's send route."""
    client = _RecordingClient([_mailer_sent()])

    send_via_mailer(_internal_message(), _mailer_config(), client=client)

    assert client.calls == [
        {
            "url": "https://mailer.test/v1/send",
            "json": {
                "to": ["staff@ucsh.com"],
                "subject": "Your code",
                "fromName": "UC Velocity",
                "html": "<p>Code <strong>123456</strong></p>\n",
                "text": "Code 123456\n",
            },
            "timeout": MAILER_TIMEOUT_SECONDS,
            "headers": {
                "authorization": "Bearer ucshm_velocity_test",
                "idempotency-key": mailer_idempotency_key("ema_internal_1"),
            },
        }
    ]


def test_mailer_idempotency_key_is_stable_and_visible_ascii() -> None:
    """Give every Clerk retry of one email the same mailer key."""
    key = mailer_idempotency_key("ema_internal_1")

    assert key == mailer_idempotency_key("ema_internal_1")
    assert key != mailer_idempotency_key("ema_internal_2")
    assert 1 <= len(key) <= 255
    assert all(33 <= ord(ch) <= 126 for ch in key)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("staff@ucsh.com", True),
        ("Staff@UCSH.COM", True),
        ("staff@example.com", False),
        ("staff@mail.ucsh.com", False),
        ("staff@ucsh.com.evil.test", False),
        ("ucsh.com", False),
    ],
)
def test_internal_recipient_needs_an_exact_domain(address: str, expected: bool) -> None:
    """Match the domain exactly, ignoring case, never a suffix or subdomain."""
    assert is_internal_recipient(address, ("ucsh.com",)) is expected


def test_internal_recipient_goes_to_mailer_and_commits_receipt() -> None:
    """Route a ucsh.com code to the mailer, never to SMTP2GO."""
    store = _Store()
    client = _RecordingClient([_mailer_sent()])

    sent = deliver_clerk_email(
        _Session(store),
        _internal_message(),
        config=_config(),
        mailer_config=_mailer_config(),
        client=client,
    )

    assert sent is True
    assert store.receipts == {"ema_internal_1"}
    assert [call["url"] for call in client.calls] == ["https://mailer.test/v1/send"]


def test_outside_recipient_stays_on_smtp2go_with_mailer_configured() -> None:
    """Keep every non-internal address on SMTP2GO, which HVE cannot reach."""
    client = _RecordingClient([_accepted_response()])

    deliver_clerk_email(
        _Session(_Store()),
        _message(),
        config=_config(),
        mailer_config=_mailer_config(),
        client=client,
    )

    assert [call["url"] for call in client.calls] == [SMTP2GO_ENDPOINT]


def test_internal_recipient_stays_on_smtp2go_without_mailer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behave exactly as before until the mailer variables are set."""
    monkeypatch.delenv("MAILER_URL", raising=False)
    monkeypatch.delenv("MAILER_KEY", raising=False)
    client = _RecordingClient([_accepted_response()])

    deliver_clerk_email(
        _Session(_Store()), _internal_message(), config=_config(), client=client
    )

    assert [call["url"] for call in client.calls] == [SMTP2GO_ENDPOINT]


def test_mailer_rejection_leaves_no_receipt_and_logs_only_the_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fail retryably on a refusal and keep content and key out of the logs."""
    store = _Store()
    refused = httpx.Response(
        503,
        json={"error": "hve_unavailable", "message": "Code 123456 to staff@ucsh.com"},
    )
    client = _RecordingClient([refused])

    with _capture_provider_logs(caplog), pytest.raises(EmailSendError):
        deliver_clerk_email(
            _Session(store),
            _internal_message(),
            mailer_config=_mailer_config(),
            client=client,
        )

    assert store.receipts == set()
    assert (
        "mailer outcome=http_rejected http_status=503 error_code=hve_unavailable"
        in caplog.text
    )
    for secret in ("123456", "staff@ucsh.com", "ucshm_velocity_test"):
        assert secret not in caplog.text


def test_mailer_network_error_is_retryable_send_error() -> None:
    """Treat an unreachable mailer as a failed send so Clerk retries."""
    client = _RecordingClient([httpx.ConnectError("refused")])

    with pytest.raises(EmailSendError):
        send_via_mailer(_internal_message(), _mailer_config(), client=client)


@pytest.mark.parametrize(
    "body",
    [
        {"status": "sent", "recipients": 2},
        {"status": "sent", "recipients": True},
        {"status": "queued", "recipients": 1},
        {"recipients": 1},
        ["sent"],
    ],
)
def test_mailer_unexpected_success_shape_is_refused(body: object) -> None:
    """Accept only one sent (or logged) recipient as proof of delivery."""
    client = _RecordingClient([httpx.Response(200, json=body)])

    with pytest.raises(EmailSendError):
        send_via_mailer(_internal_message(), _mailer_config(), client=client)


def test_mailer_log_mode_is_accepted_but_flagged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Accept the mailer's no-send mode but log it as a warning."""
    client = _RecordingClient(
        [httpx.Response(200, json={"status": "logged", "recipients": 1})]
    )

    with _capture_provider_logs(caplog):
        send_via_mailer(_internal_message(), _mailer_config(), client=client)

    assert "mailer outcome=logged" in caplog.text


def test_mailer_config_absent_or_half_set_is_none(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fall back to SMTP2GO unless both mailer variables are set."""
    monkeypatch.delenv("MAILER_URL", raising=False)
    monkeypatch.delenv("MAILER_KEY", raising=False)
    assert load_mailer_config() is None

    monkeypatch.setenv("MAILER_URL", "https://mailer.test")
    with _capture_provider_logs(caplog):
        assert load_mailer_config() is None
    assert "mailer outcome=not_configured missing=MAILER_KEY" in caplog.text


def test_mailer_config_reads_url_key_and_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trim the URL's trailing slash and default the domains to ucsh.com."""
    monkeypatch.setenv("MAILER_URL", "https://mailer.test/")
    monkeypatch.setenv("MAILER_KEY", " ucshm_velocity_test ")
    monkeypatch.delenv("MAILER_DOMAINS", raising=False)

    config = load_mailer_config()

    assert config is not None
    assert config.url == "https://mailer.test"
    assert config.key == "ucshm_velocity_test"
    assert config.domains == ("ucsh.com",)
    assert "ucshm_velocity_test" not in repr(config)

    monkeypatch.setenv("MAILER_DOMAINS", " UCSH.com, ucaccess.com ,")
    config = load_mailer_config()
    assert config is not None
    assert config.domains == ("ucsh.com", "ucaccess.com")
