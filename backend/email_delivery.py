"""Deliver authenticated Clerk messages through SMTP2GO exactly once normally.

The transaction-scoped advisory lock serializes one Clerk email id across all
Gunicorn workers. A durable receipt is committed only after SMTP2GO accepts the
message, allowing Clerk to retry failures without duplicating completed sends.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from threading import Lock
from typing import Protocol

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from clerk_email import ClerkEmailMessage


# Use SMTP2GO's official v3 endpoint rather than deployment configuration.
SMTP2GO_ENDPOINT = "https://api.smtp2go.com/v3/email/send"
# Bound DNS, connection, write, and response waits for webhook retries.
SMTP2GO_TIMEOUT_SECONDS = 8.0
# Bound each database statement while the delivery transaction is open.
DATABASE_STATEMENT_TIMEOUT = "5000ms"
# Reserve a small pool so slow email delivery cannot occupy the ERP request pool.
EMAIL_DATABASE_POOL_SIZE = 2
EMAIL_DATABASE_POOL_TIMEOUT_SECONDS = 1
EMAIL_DATABASE_CONNECT_TIMEOUT_SECONDS = 5

# Build the dedicated pool only after an authenticated email actually needs it.
_email_session_factory: sessionmaker[Session] | None = None
_email_session_factory_lock = Lock()


class EmailDeliveryError(RuntimeError):
    """Base class for sanitized, retryable delivery failures."""


class EmailDeliveryConfigurationError(EmailDeliveryError):
    """Raised when required SMTP2GO configuration is unavailable."""


class EmailDeliveryBusy(EmailDeliveryError):
    """Raised when another worker is delivering the same Clerk message."""


class EmailSendError(EmailDeliveryError):
    """Raised when SMTP2GO does not accept exactly one message."""


class EmailDeliveryPersistenceError(EmailDeliveryError):
    """Raised when the delivery receipt cannot be committed safely."""


class HttpClient(Protocol):
    """Describe the provider call used by production and mocked tests."""

    def post(
        self,
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> httpx.Response:
        """Submit one JSON request and return its response."""


@dataclass(frozen=True, repr=False)
class EmailDeliveryConfig:
    """Hold deployment-owned SMTP2GO credentials and sender identity."""

    api_key: str
    sender: str


def load_email_delivery_config() -> EmailDeliveryConfig:
    """Load required SMTP2GO settings without failing application startup.

    Returns:
        Validated provider configuration for one delivery attempt.

    Raises:
        EmailDeliveryConfigurationError: If the API key or approved sender is
            missing.
    """
    # Read secrets only when a selected webhook actually needs delivery.
    api_key = (os.getenv("SMTP2GO_API_KEY") or "").strip()
    sender = (os.getenv("SMTP2GO_SENDER") or "").strip()
    if not api_key or not sender:
        raise EmailDeliveryConfigurationError("email delivery is not configured")
    return EmailDeliveryConfig(api_key=api_key, sender=sender)


def clerk_email_lock_key(email_id: str) -> int:
    """Map a Clerk email id to a stable namespaced signed PostgreSQL key.

    Args:
        email_id: Clerk's opaque email resource id.

    Returns:
        A deterministic signed 64-bit advisory-lock key.
    """
    # Namespace the digest to reduce collisions with other advisory-lock uses.
    digest = hashlib.sha256(
        b"uc-velocity:clerk-email:" + email_id.encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def send_via_smtp2go(
    message: ClerkEmailMessage,
    config: EmailDeliveryConfig,
    *,
    client: HttpClient | None = None,
) -> None:
    """Send Clerk's exact subject and bodies through the approved sender.

    Args:
        message: Authenticated and validated Clerk email content.
        config: Deployment-owned SMTP2GO API key and sender.
        client: Optional HTTP client supplied by tests.

    Raises:
        EmailSendError: If the provider is unreachable, returns malformed data,
            or does not accept exactly one message.
    """
    payload: dict[str, object] = {
        # Credentials and sender always come from Railway, never the webhook.
        "api_key": config.api_key,
        "sender": config.sender,
        "to": [message.to_email],
        "subject": message.subject,
    }
    if message.html_body is not None:
        # Forward Clerk's decoded HTML string without rendering or rewriting it.
        payload["html_body"] = message.html_body
    if message.text_body is not None:
        # Forward Clerk's decoded plain text without rendering or rewriting it.
        payload["text_body"] = message.text_body

    owned_client = client is None
    provider_client: HttpClient = client or httpx.Client()
    try:
        try:
            response = provider_client.post(
                SMTP2GO_ENDPOINT,
                json=payload,
                timeout=SMTP2GO_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            # Never expose exceptions that could include credentials or content.
            raise EmailSendError("could not reach the email service") from exc
    finally:
        if owned_client:
            # The locally created client owns its connection resources.
            assert isinstance(provider_client, httpx.Client)
            provider_client.close()
    if not 200 <= response.status_code < 300:
        raise EmailSendError("email service rejected the request")
    try:
        result = response.json()
    except (TypeError, ValueError) as exc:
        raise EmailSendError("email service returned an invalid response") from exc
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        raise EmailSendError("email service returned an invalid response")
    provider_data = result["data"]
    succeeded = provider_data.get("succeeded")
    failed = provider_data.get("failed")
    # Reject bools because they are Python ints but not provider count values.
    if type(succeeded) is not int or type(failed) is not int:
        raise EmailSendError("email service returned an invalid response")
    if succeeded != 1 or failed != 0:
        raise EmailSendError("the email was not accepted for delivery")


def _get_email_session_factory() -> sessionmaker[Session]:
    """Create the dedicated bounded email database pool on first delivery.

    Returns:
        A session factory backed by a two-connection email-only pool.

    Raises:
        EmailDeliveryPersistenceError: If DATABASE_URL is missing or the
            dedicated SQLAlchemy engine cannot be configured.
    """
    global _email_session_factory
    if _email_session_factory is not None:
        return _email_session_factory
    # Serialize first construction when two workers' threads start together.
    with _email_session_factory_lock:
        if _email_session_factory is not None:
            return _email_session_factory
        database_url = (os.getenv("DATABASE_URL") or "").strip()
        if not database_url:
            raise EmailDeliveryPersistenceError("email database is not configured")
        try:
            # A dedicated bounded pool isolates provider latency from ERP traffic.
            engine = create_engine(
                database_url,
                pool_pre_ping=True,
                pool_size=EMAIL_DATABASE_POOL_SIZE,
                max_overflow=0,
                pool_timeout=EMAIL_DATABASE_POOL_TIMEOUT_SECONDS,
                connect_args={
                    "connect_timeout": EMAIL_DATABASE_CONNECT_TIMEOUT_SECONDS
                },
            )
            _email_session_factory = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=engine,
            )
        except SQLAlchemyError as exc:
            raise EmailDeliveryPersistenceError(
                "could not configure email database"
            ) from exc
        return _email_session_factory


def run_delivery(message: ClerkEmailMessage) -> bool:
    """Open one dedicated email session and complete a delivery attempt.

    Args:
        message: Authenticated Clerk email selected for external delivery.

    Returns:
        ``True`` for a newly committed delivery or ``False`` for a duplicate.

    Raises:
        EmailDeliveryError: If configuration, provider, lock, pool, or database
            work prevents a safely committed result.
    """
    factory = _get_email_session_factory()
    try:
        # Close the request session while retaining the process-level small pool.
        with factory() as db:
            return deliver_clerk_email(db, message)
    except EmailDeliveryError:
        raise
    except SQLAlchemyError as exc:
        # Pool exhaustion and connection failures remain sanitized and retryable.
        raise EmailDeliveryPersistenceError(
            "could not access email database"
        ) from exc


def deliver_clerk_email(
    db: Session,
    message: ClerkEmailMessage,
    *,
    config: EmailDeliveryConfig | None = None,
    client: HttpClient | None = None,
) -> bool:
    """Coordinate provider acceptance and a durable receipt transactionally.

    Args:
        db: A fresh synchronous SQLAlchemy session from the email-only pool.
        message: Authenticated Clerk email selected for external delivery.
        config: Optional provider configuration supplied by tests.
        client: Optional provider HTTP client supplied by tests.

    Returns:
        ``True`` after sending and committing a receipt, or ``False`` when an
        existing receipt proves an earlier attempt already completed.

    Raises:
        EmailDeliveryBusy: If another worker holds this email's advisory lock.
        EmailDeliveryConfigurationError: If provider settings are missing.
        EmailSendError: If SMTP2GO does not accept the message.
        EmailDeliveryPersistenceError: If database work or commit fails.
    """
    try:
        # Commit the receipt and release the transaction-scoped lock together.
        with db.begin():
            # Apply a server-side bound to all following SQL in this transaction.
            db.execute(
                text("SELECT set_config('statement_timeout', :timeout, true)"),
                {"timeout": DATABASE_STATEMENT_TIMEOUT},
            )
            lock_result = db.execute(
                text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
                {"lock_key": clerk_email_lock_key(message.email_id)},
            ).scalar_one()
            if lock_result is not True:
                raise EmailDeliveryBusy("email delivery is already in progress")
            already_delivered = db.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM clerk_email_deliveries "
                    "WHERE clerk_email_id = :email_id)"
                ),
                {"email_id": message.email_id},
            ).scalar_one()
            if already_delivered is True:
                # A committed receipt makes Clerk's retry an acknowledged no-op.
                return False
            # Load live settings only after proving that a send is still needed.
            delivery_config = config or load_email_delivery_config()
            send_via_smtp2go(message, delivery_config, client=client)
            # Record no recipient, subject, body, or one-time code.
            db.execute(
                text(
                    "INSERT INTO clerk_email_deliveries (clerk_email_id) "
                    "VALUES (:email_id)"
                ),
                {"email_id": message.email_id},
            )
        # Return only after the context manager has committed successfully.
        return True
    except (EmailDeliveryBusy, EmailDeliveryConfigurationError, EmailSendError):
        # Preserve sanitized domain failures after automatic transaction rollback.
        raise
    except SQLAlchemyError as exc:
        # Hide SQL text, connection details, and driver diagnostics from callers.
        raise EmailDeliveryPersistenceError(
            "could not persist email delivery"
        ) from exc
