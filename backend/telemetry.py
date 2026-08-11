"""Drop-in telemetry client for a Python app. Copy this ONE file into your project.

    from telemetry import Telemetry

    tel = Telemetry(source="velocity", app_version="1.4.2")   # reads env for url/key
    tel.action("report_generated", {"rows": 812})
    tel.error("backend_call", endpoint="/api/things/42", status_code=500)

No dependencies (stdlib only) and no configuration beyond two environment variables.

THE ONE RULE THIS FILE ENFORCES: telemetry must never break, slow, or crash the app
it is measuring. Every public method is non-blocking and swallows its own errors, an
unconfigured client is a total no-op, and a dead/slow service costs the caller
nothing — events are dropped, not retried forever, and never surfaced.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

BATCH_SIZE = 20          # events per POST
FLUSH_SECONDS = 30.0     # how long an event may wait before being sent
MAX_QUEUE = 500          # hard cap; past this the OLDEST events are dropped
TIMEOUT_SECONDS = 10.0
MAX_MESSAGE_LEN = 500    # error messages are truncated, not stored whole


def _scrub(text: str) -> str:
    """Replace the user's home directory with ~ so a Windows username never leaves
    the machine inside a stack trace or file path."""
    home = str(Path.home())
    return text.replace(home, "~").replace(home.replace("\\", "\\\\"), "~")


class Telemetry:
    """A batching, fire-and-forget event emitter.

    Args:
        source: stable slug identifying THIS app, e.g. "velocity". Never change it
            once data exists — every chart groups on it.
        app_version: your app's version string; powers version-adoption reporting.
        url / key: default to TELEMETRY_URL / TELEMETRY_KEY. If either is missing the
            client is DORMANT — no id, no thread, no network, no files.
        install_id_path: where the anonymous per-machine id is persisted. Defaults to
            a dotfile beside the user's home. Pass your own for a server app.
    """

    def __init__(
        self,
        source: str,
        app_version: Optional[str] = None,
        url: Optional[str] = None,
        key: Optional[str] = None,
        os_name: Optional[str] = None,
        install_id_path: Optional[Path] = None,
    ) -> None:
        self.source = source
        self.app_version = app_version
        self.url = (url if url is not None else os.environ.get("TELEMETRY_URL", "")).strip()
        self.key = (key if key is not None else os.environ.get("TELEMETRY_KEY", "")).strip()
        self.os_name = os_name or os.name
        self.enabled = bool(self.url and self.key)

        self._queue: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._user_id: Optional[str] = None
        self._user_name: Optional[str] = None

        if not self.enabled:
            # Dormant: do not create an install id, a session id, or a file.
            self.install_id = ""
            self.session_id = ""
            return

        self.install_id = self._load_install_id(install_id_path)
        self.session_id = str(uuid.uuid4())
        atexit.register(self.flush)

    # --- identity ---------------------------------------------------------------

    def _load_install_id(self, path: Optional[Path]) -> str:
        """A stable anonymous id for this machine. It is a random UUID — it contains
        nothing about the user — and it survives upgrades because it lives in a file
        the installer does not touch. Unwritable disk falls back to a per-run id
        rather than raising."""
        path = path or Path.home() / f".{self.source}-install-id"
        try:
            if path.exists():
                existing = path.read_text(encoding="utf-8").strip()
                if existing:
                    return existing
            new = str(uuid.uuid4())
            path.write_text(new, encoding="utf-8")
            return new
        except Exception:
            return str(uuid.uuid4())

    def identify(self, user_id: Optional[str], user_name: Optional[str] = None) -> None:
        """Attach a signed-in person to everything sent from here on. Use YOUR app's
        internal user id — never an email, and never a token."""
        self._user_id = str(user_id) if user_id is not None else None
        self._user_name = user_name

    # --- emitting ---------------------------------------------------------------

    def action(self, name: str, props: Optional[dict[str, Any]] = None, **fields: Any) -> None:
        """Something the user did, or a call your app made. This is the denominator
        of the error rate, so record attempts — not successes."""
        self._enqueue("action", name, props, fields)

    def error(self, name: str, props: Optional[dict[str, Any]] = None, **fields: Any) -> None:
        """Something that failed. Pass endpoint/status_code/error_class/error_message
        as keyword arguments where you have them."""
        self._enqueue("error", name, props, fields)

    def lifecycle(self, name: str, props: Optional[dict[str, Any]] = None) -> None:
        """app_start / app_exit and friends. Deliberately excluded from the error-rate
        denominator: a launch is not an attempt at anything."""
        self._enqueue("lifecycle", name, props, {})

    def _enqueue(
        self, event_type: str, name: str,
        props: Optional[dict[str, Any]], fields: dict[str, Any],
    ) -> None:
        if not self.enabled:
            return
        try:
            message = fields.get("error_message")
            event = {
                "event_id": str(uuid.uuid4()),   # idempotency key; server dedups on it
                "event_type": event_type,
                "event_name": name,
                "props": props or None,
                "endpoint": fields.get("endpoint"),
                "http_method": fields.get("http_method"),
                "status_code": fields.get("status_code"),
                "error_class": fields.get("error_class"),
                "error_message": (
                    _scrub(str(message))[:MAX_MESSAGE_LEN] if message else None
                ),
                "duration_ms": fields.get("duration_ms"),
            }
            with self._lock:
                self._queue.append(event)
                # Drop the OLDEST on overflow: during an outage the newest events are
                # the ones describing it.
                if len(self._queue) > MAX_QUEUE:
                    del self._queue[: len(self._queue) - MAX_QUEUE]
                ready = len(self._queue) >= BATCH_SIZE
            self._schedule() if not ready else self.flush()
        except Exception:
            pass  # never let measurement break the thing being measured

    # --- sending ----------------------------------------------------------------

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                return
            # daemon: a pending flush must never hold a CLI or a container open.
            self._timer = threading.Timer(FLUSH_SECONDS, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        """Send whatever is queued, in the background. Safe to call anytime."""
        if not self.enabled:
            return
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            batch, self._queue = self._queue[:BATCH_SIZE], self._queue[BATCH_SIZE:]
            more = bool(self._queue)
        if not batch:
            return
        thread = threading.Thread(target=self._post, args=("/events", {
            "source": self.source,
            "app_version": self.app_version,
            "os": self.os_name,
            "install_id": self.install_id,
            "session_id": self.session_id,
            "user_id": self._user_id,
            "events": batch,
        }, batch), daemon=True)
        thread.start()
        if more:
            self._schedule()

    def feedback(
        self, message: str, title: Optional[str] = None,
        category: Optional[str] = None, region: Optional[str] = None,
    ) -> None:
        """A note typed by a user, sent straight to the dashboard's feedback board."""
        if not self.enabled or not message.strip():
            return
        threading.Thread(target=self._post, args=("/feedback", {
            "source": self.source,
            "app_version": self.app_version,
            "os": self.os_name,
            "install_id": self.install_id,
            "session_id": self.session_id,
            "user_id": self._user_id,
            "user_name": self._user_name,
            "region": region,
            "category": category,
            "title": title,
            "message": message.strip(),
            "event_id": str(uuid.uuid4()),
        }, None), daemon=True).start()

    def _post(self, path: str, payload: dict[str, Any], retry: Any) -> None:
        request = urllib.request.Request(
            self.url.rstrip("/") + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
                return
        except Exception as exc:
            # Put events BACK only for a transient failure. A 4xx means the service
            # rejected this payload and always will, so requeuing it would loop
            # forever; the event_id makes a re-send after a lost ack harmless.
            transient = not (
                isinstance(exc, urllib.error.HTTPError) and 400 <= exc.code < 500
            )
            if retry and transient:
                with self._lock:
                    self._queue = (list(retry) + self._queue)[-MAX_QUEUE:]
