"""Fire-and-forget request telemetry for UC Velocity.

Wraps every HTTP request via the shared telemetry client: an ``api_request`` action
on success, and a ``request_failed`` error on a 5xx or an unhandled exception — the
single error choke-point the integration brief asks for. This one place fills the
error-rate, latency (p95), version-adoption and install panels without
hand-instrumenting each route.

Guarantees, in order of importance:
  * It never breaks a request. The emit is non-blocking (the client batches in a
    background thread) and every telemetry call is wrapped, so a failure here can
    never surface to the caller.
  * It is inert when telemetry is unconfigured (``tel.enabled`` is False).

Attribution note: events are NOT tagged with the acting user. The drop-in client
carries one identity per envelope (it was built for a single-user desktop app), so
tagging a shared server-side client per request would misattribute events across
concurrent users. Per-user analytics is deliberately left to a follow-up; user
identity IS handled correctly on the feedback path, which sends it per request.
"""
import time
from typing import Optional

from telemetry_client import tel

# Liveness/root noise would swamp the real signal; never emit for these.
_SKIP_PATHS = {"/health", "/"}


class TelemetryMiddleware:
    """Pure-ASGI so it adds no overhead and can't interfere with response streaming."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not tel.enabled:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        if method == "OPTIONS" or path in _SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        status = {"code": 0}

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                status["code"] = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            self._emit(method, path, start, code=500, exc=exc)
            raise
        else:
            self._emit(method, path, start, code=status["code"], exc=None)

    @staticmethod
    def _emit(method: str, path: str, start: float, code: int, exc: Optional[Exception]) -> None:
        try:
            duration_ms = round((time.monotonic() - start) * 1000)
            if exc is not None:
                tel.error("request_failed", endpoint=path, http_method=method,
                          status_code=code, error_class=type(exc).__name__,
                          error_message=str(exc), duration_ms=duration_ms)
            elif code >= 500:
                tel.error("request_failed", endpoint=path, http_method=method,
                          status_code=code, duration_ms=duration_ms)
            else:
                tel.action("api_request", endpoint=path, http_method=method,
                           status_code=code, duration_ms=duration_ms)
        except Exception:
            pass  # telemetry must never break the request it measures
