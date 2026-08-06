"""Shared configuration, connections, safety guard, and type mapping.

Everything that both `stage_raw` and `verify_staging` need lives here so the two
commands connect to the same places in the same way.

Safety model (read this):
  - The target Postgres is read from `MIGRATION_DATABASE_URL` (or --database-url),
    a DISTINCT variable from the app's `DATABASE_URL`. This is deliberate: it means
    the tooling can never accidentally inherit a shell that points at production.
  - `assert_scratch_target()` refuses to run unless the caller explicitly confirms
    the target is a throwaway/scratch DB, and it hard-blocks any host that looks
    like production.
  - Even so, staging writes ONLY into the `vision_legacy` schema and never touches
    Velocity's application tables.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

# The isolated schema that holds the verbatim Vision copy. Never the app tables.
STAGING_SCHEMA = "vision_legacy"

# The isolated schema that holds a read-only copy of live Velocity, for Diff B.
# Like STAGING_SCHEMA it is never the app's own tables -- it lives on the same
# throwaway staging DB and is written only by the `load-velocity` command.
VELOCITY_CURRENT_SCHEMA = "velocity_current"

# Hosts we refuse to write to, ever. Extend via MIGRATION_BLOCKED_HOSTS (comma-sep).
# Covers Railway's private host (`railway.internal`, unreachable off-Railway) AND
# the reachable public endpoints (`rlwy.net` proxy, `railway.app`) so a prod URL
# sitting in the shell can't slip through. The check runs against the WHOLE
# connection string (see assert_scratch_target), so keyword-form DSNs are caught.
_DEFAULT_BLOCKED_HOST_MARKERS = ("railway.internal", "rlwy.net", "railway.app")

# Postgres truncates identifiers to 63 bytes; Access allows up to 64 chars.
MAX_PG_IDENTIFIER_BYTES = 63


class MigrationConfigError(RuntimeError):
    """Raised for any misconfiguration or safety-guard violation."""


# --------------------------------------------------------------------------- #
# Vision (Access) connection
# --------------------------------------------------------------------------- #

def vision_connection_string(mdb_path: str, workgroup_path: str) -> str:
    """Build the ACE OLEDB connection string that unlocks Vision's user-level
    security via the workgroup (.mdw) file. This is the exact form proven to read
    the ULS-protected backend as the Admin user with `Share Deny None` (so it can
    read while Access has the file open).
    """
    if not mdb_path or not os.path.exists(mdb_path):
        raise MigrationConfigError(f"Vision .mdb not found: {mdb_path!r}")
    if not workgroup_path or not os.path.exists(workgroup_path):
        raise MigrationConfigError(f"Workgroup .mdw not found: {workgroup_path!r}")
    return (
        "Provider=Microsoft.ACE.OLEDB.12.0;"
        f"Data Source={mdb_path};"
        f"Jet OLEDB:System Database={workgroup_path};"
        "Mode=Share Deny None;"
    )


def open_vision(mdb_path: str, workgroup_path: str):
    """Open an ADODB connection to the Vision backend (returns the COM object).

    Uses pywin32's late-bound COM. Imported lazily so `--help` and unit imports
    work on machines without pywin32 (e.g. Linux CI).
    """
    try:
        import win32com.client  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment specific
        raise MigrationConfigError(
            "pywin32 is required to read Access. Install this package's "
            "requirements: pip install -r requirements.txt (Windows only)."
        ) from exc
    conn = win32com.client.Dispatch("ADODB.Connection")
    conn.Open(vision_connection_string(mdb_path, workgroup_path))
    return conn


# --------------------------------------------------------------------------- #
# Postgres (target) connection + guard
# --------------------------------------------------------------------------- #

def resolve_target_url(cli_url: str | None) -> str:
    """The target Postgres URL: --database-url wins, else MIGRATION_DATABASE_URL.

    Intentionally does NOT fall back to the app's DATABASE_URL.
    """
    url = cli_url or os.getenv("MIGRATION_DATABASE_URL")
    if not url:
        raise MigrationConfigError(
            "No target database. Pass --database-url or set MIGRATION_DATABASE_URL "
            "to a SCRATCH/preview Postgres (never production)."
        )
    return url


def resolve_velocity_source_url(cli_url: str | None) -> str:
    """Resolve the READ-ONLY live-Velocity source url for Diff B.

    Kept deliberately as a THIRD variable, distinct from both the app's
    DATABASE_URL and the staging *target* url, so the loader can never confuse
    "where I read live data" with "where I write the copy". It is never run
    through the prod-host guard -- that guard protects write *targets*; reading
    live Velocity is fine and the loader only issues SELECTs.

    Args:
        cli_url: The value of ``--velocity-source-url`` (or ``None`` if omitted).

    Returns:
        The source url: the CLI value if given, else ``MIGRATION_VELOCITY_SOURCE_URL``.

    Raises:
        MigrationConfigError: If neither the CLI arg nor the env var is set.
    """
    url = cli_url or os.getenv("MIGRATION_VELOCITY_SOURCE_URL")   # CLI wins, else env var
    if not url:                                                    # nothing to read from -> fail loud
        raise MigrationConfigError(
            "No Velocity source. Pass --velocity-source-url or set "
            "MIGRATION_VELOCITY_SOURCE_URL to the live Velocity Postgres "
            "(read-only; e.g. the Railway public URL)."
        )
    return url


def _blocked_markers() -> tuple[str, ...]:
    extra = os.getenv("MIGRATION_BLOCKED_HOSTS", "")
    extras = tuple(m.strip() for m in extra.split(",") if m.strip())
    return _DEFAULT_BLOCKED_HOST_MARKERS + extras


def _extract_host(url: str) -> str | None:
    """Best-effort host from either a URL DSN or a libpq keyword/value DSN.

    psycopg2 accepts BOTH `postgresql://user:pw@host/db` and the keyword form
    `host=... dbname=... user=...`. urlparse only understands the first, so we
    parse the keyword form ourselves -- otherwise a keyword-form prod DSN would
    slip past a hostname-only guard.
    """
    parsed = urlparse(url)
    if parsed.hostname:
        return parsed.hostname
    for token in url.replace("\t", " ").split():
        if token.lower().startswith("host="):
            return token[len("host="):].strip("'\"") or None
    return None


def assert_scratch_target(url: str, confirmed: bool) -> str:
    """Refuse to proceed unless the target is an explicitly-confirmed non-prod DB.

    Fails CLOSED: the blocked-marker check runs against the whole connection
    string (so URL *and* keyword-form DSNs are covered), and an indeterminate
    host is refused rather than assumed safe. Returns the host for logging.
    """
    raw = url.lower()
    for marker in _blocked_markers():
        if marker and marker.lower() in raw:
            raise MigrationConfigError(
                f"Refusing to run: connection string matches blocked marker "
                f"{marker!r}. This looks like production."
            )
    host = (_extract_host(url) or "").lower()
    if not host:
        raise MigrationConfigError(
            "Could not determine the target host from the connection string. "
            "Refusing (fail-closed) -- use an explicit host, e.g. "
            "postgresql://postgres:postgres@localhost:5432/vision_scratch."
        )
    if not confirmed:
        raise MigrationConfigError(
            f"Target host is {host!r}. Re-run with --i-understand-scratch-db to confirm "
            "this is a throwaway/preview database, not production."
        )
    return host


def check_identifier_length(name: str, kind: str = "identifier") -> None:
    """Fail loud if a name exceeds Postgres' 63-byte identifier limit.

    Access permits up to 64-char names; Postgres would silently truncate to 63,
    which could collide two names or break the verify round-trip. Naming the
    offender is far better than a silent truncation.
    """
    if len(name.encode("utf-8")) > MAX_PG_IDENTIFIER_BYTES:
        raise MigrationConfigError(
            f"{kind} {name!r} exceeds Postgres' {MAX_PG_IDENTIFIER_BYTES}-byte "
            "identifier limit; staging would truncate/collide. Add a name mapping."
        )


def open_postgres(url: str):
    """Open a psycopg2 connection. Imported lazily for the same reason as pywin32."""
    try:
        import psycopg2  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment specific
        raise MigrationConfigError(
            "psycopg2 is required. Install this package's requirements: "
            "pip install -r requirements.txt"
        ) from exc
    return psycopg2.connect(url)


# --------------------------------------------------------------------------- #
# Access (ADO DataTypeEnum) -> Postgres type mapping
# --------------------------------------------------------------------------- #
# ADO DataTypeEnum values we expect from Vision. Full list is large; we map the
# ones Vision actually uses and fall back to TEXT for anything unexpected.
_ADO = {
    2: "adSmallInt", 3: "adInteger", 4: "adSingle", 5: "adDouble",
    6: "adCurrency", 7: "adDate", 11: "adBoolean", 14: "adDecimal",
    16: "adTinyInt", 17: "adUnsignedTinyInt", 18: "adUnsignedSmallInt",
    19: "adUnsignedInt", 20: "adBigInt", 72: "adGUID", 128: "adBinary",
    129: "adChar", 130: "adWChar", 131: "adNumeric", 133: "adDBDate",
    135: "adDBTimeStamp", 200: "adVarChar", 201: "adLongVarChar",
    202: "adVarWChar", 203: "adLongVarWChar", 204: "adVarBinary",
    205: "adLongVarBinary",
}

# Which ADO types land in a Postgres BYTEA column (need psycopg2.Binary wrapping).
BINARY_ADO_TYPES = {128, 204, 205}
# Which ADO types are date/time (need pywintypes->datetime coercion on load).
DATETIME_ADO_TYPES = {7, 133, 135}


def access_type_to_pg(ado_type: int) -> str:
    """Map an ADO field type to a Postgres column type for the staging mirror.

    Deliberately generous: staging is a faithful copy, not a normalized schema,
    so we prefer wide, lossless types (TEXT, DOUBLE PRECISION, NUMERIC, BYTEA).
    """
    if ado_type in (2, 3, 16, 17, 18, 19):
        return "INTEGER"
    if ado_type == 20:
        return "BIGINT"
    if ado_type in (4, 5):
        return "DOUBLE PRECISION"
    if ado_type in (6, 14, 131):
        return "NUMERIC"
    if ado_type == 11:
        return "BOOLEAN"
    if ado_type in DATETIME_ADO_TYPES:
        return "TIMESTAMP"
    if ado_type in BINARY_ADO_TYPES:
        return "BYTEA"
    # adChar/adWChar/adVarChar/adLongVar*/adGUID and anything unmapped -> TEXT
    return "TEXT"


def ado_type_name(ado_type: int) -> str:
    return _ADO.get(ado_type, f"ado({ado_type})")
