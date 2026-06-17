"""Shared backend utilities."""
from datetime import datetime, timezone


def to_naive_utc(dt: datetime) -> datetime:
    """
    Normalize an incoming datetime to naive-UTC for storage.

    created_at columns are stored naive-UTC (datetime.utcnow). A datetime-local
    input is local wall-clock, so the frontend converts it to a UTC ISO-8601 string
    (e.g. via ``new Date(localValue).toISOString()``, which ends in ``Z``). Pydantic
    parses that into a tz-aware datetime; we convert it back to UTC and drop the
    tzinfo so it matches the stored naive-UTC convention.

    A naive datetime (no offset supplied) is assumed to already be UTC and returned
    unchanged. This keeps a single round-trip convention so the stored time does not
    silently shift by the browser's offset.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
