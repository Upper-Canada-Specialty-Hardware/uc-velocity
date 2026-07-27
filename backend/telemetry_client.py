"""UC Velocity's wiring for the shared UCSH telemetry service.

The drop-in client lives verbatim in ``telemetry.py``; this module owns the ONE
process-wide emitter and the small helpers the feedback proxy needs. Everything is
INERT until ``TELEMETRY_URL`` and ``TELEMETRY_KEY`` are both set — ``tel`` then sends
nothing, ``TELEMETRY_ENABLED`` is False, and the app behaves exactly as before. That
dormant state is the intended default until the service is switched on.

Why a backend integration (not the browser): the ingest key is write-only but
effectively public, so keeping it server-side avoids shipping it in the JS bundle and
means the telemetry service needs no CORS origin for Velocity.
"""
import hashlib
import os

from telemetry import Telemetry

TELEMETRY_URL = os.getenv("TELEMETRY_URL", "").strip().rstrip("/")
TELEMETRY_KEY = os.getenv("TELEMETRY_KEY", "").strip()
TELEMETRY_SOURCE = (os.getenv("TELEMETRY_SOURCE") or "velocity").strip()
APP_VERSION = os.getenv("APP_VERSION") or os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
TELEMETRY_ENABLED = bool(TELEMETRY_URL and TELEMETRY_KEY)

# A user's feedback thread is keyed on this opaque, STABLE hash rather than the raw
# Clerk id. The telemetry service scopes threads by "install_id"; hashing means the
# id can't be walked by guessing, the raw Clerk id never leaves Velocity, and the
# same user reaches the same thread from any browser or device. Optional salt.
_THREAD_SALT = os.getenv("TELEMETRY_THREAD_SALT", "")

# One stable logical "install" for the server so analytics counts one backend, not
# one-per-restart. A single web backend is genuinely one install.
_INSTALL_ID = (os.getenv("TELEMETRY_INSTALL_ID") or "velocity-server").strip()

# The single, process-wide, fire-and-forget analytics emitter.
tel = Telemetry(source=TELEMETRY_SOURCE, app_version=APP_VERSION)

# The drop-in client persists its install id to a file (it was built for a desktop
# app); a web backend has no stable disk, so pin a fixed logical id instead. Only
# meaningful when enabled — a dormant client keeps install_id="" and sends nothing.
if tel.enabled:
    tel.install_id = _INSTALL_ID


def feedback_thread_key(user_id: str) -> str:
    """Opaque, stable per-user id used as the telemetry ``install_id`` for feedback."""
    digest = hashlib.sha256(f"{_THREAD_SALT}{user_id}".encode("utf-8")).hexdigest()
    return f"velocity-user-{digest[:40]}"
