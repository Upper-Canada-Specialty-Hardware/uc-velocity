"""Shared configuration, connections, safety guard, and type mapping.

Everything that both `stage_raw` and `verify_staging` need lives here so the two
commands connect to the same places in the same way.

Safety model (read this):
  - The target Postgres is read from `MIGRATION_DATABASE_URL` (or --database-url),
    a DISTINCT variable from the app's `DATABASE_URL`. This is deliberate: it means
    the tooling can never accidentally inherit a shell that points at production.
  - `assert_scratch_target()` refuses to run unless the caller explicitly confirms
    the target is a throwaway/scratch DB, and it hard-blocks any host that looks
    like production. The only way through is `MIGRATION_UNLOCK_HOST` naming ONE
    exact host (the cutover mechanism) -- and the confirmation flag is still
    required on top of that.
  - Even so, staging writes ONLY into the `vision_legacy` schema and never touches
    Velocity's application tables.
"""
from __future__ import annotations

import os
import sys
from urllib.parse import parse_qs, urlparse

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

# The ONE deliberate way past the blocklist: MIGRATION_UNLOCK_HOST names a single
# exact endpoint as "host:port" (the production proxy endpoint for the cutover, or
# a PR-preview endpoint for a dress rehearsal). Only a DSN whose *parsed* host AND
# port both equal it (host case-insensitive) skips the marker check. The port is
# mandatory because Railway's public proxies are shared regional hostnames --
# several databases sit behind one "<name>.proxy.rlwy.net" told apart only by port,
# so a hostname alone could unlock production while meaning a preview. A substring
# such as "rlwy.net" unlocks nothing. Even when unlocked, the REST of the DSN is
# still scanned for blocked markers and multi-host / hostaddr / query overrides are
# refused, so the connection can only ever reach the one named endpoint. The CLI
# confirmation flag is STILL required, and every run against an unlocked endpoint
# prints a loud banner. Unset the variable afterwards and the guard re-locks by
# itself; nothing is persisted anywhere.
UNLOCK_HOST_ENV = "MIGRATION_UNLOCK_HOST"

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


def _unlock_endpoint() -> tuple[str, int] | None:
    """Read the deliberately-unlocked endpoint, if any.

    Returns:
        ``(host, port)`` from ``MIGRATION_UNLOCK_HOST`` (host lower-cased), or None
        when the variable is unset/blank (the normal, fully-locked state).

    Raises:
        MigrationConfigError: the variable is set but is not ``host:port`` -- a
            malformed unlock is refused loudly rather than silently ignored, so an
            operator can't believe they unlocked something they didn't.
    """
    value = os.getenv(UNLOCK_HOST_ENV, "").strip().lower()   # blank counts as unset
    if not value:
        return None
    host, sep, port = value.rpartition(":")                   # split on the LAST colon
    if not sep or not host or not port.isdigit():             # must be exactly host:port
        raise MigrationConfigError(
            f"{UNLOCK_HOST_ENV} must be 'host:port' naming ONE exact endpoint "
            f"(e.g. nozomi.proxy.rlwy.net:52032); got {value!r}."
        )
    return host, int(port)


def _extract_host(url: str) -> str | None:
    """Best-effort host from either a URL DSN or a libpq keyword/value DSN.

    psycopg2 accepts BOTH `postgresql://user:pw@host/db` and the keyword form
    `host=... dbname=... user=...`. urlparse only understands the first, so we
    parse the keyword form ourselves -- otherwise a keyword-form prod DSN would
    slip past a hostname-only guard.
    """
    try:
        parsed = urlparse(url)
    except ValueError:                                         # e.g. an unbalanced '[' -> unparseable
        return None                                            # caller treats "no host" as fail-closed
    if parsed.hostname:
        return parsed.hostname
    for token in url.replace("\t", " ").split():
        if token.lower().startswith("host="):
            return token[len("host="):].strip("'\"") or None
    return None


def _extract_port(url: str) -> int | None:
    """Best-effort port from a URL DSN or a libpq keyword/value DSN.

    Returns:
        The port as an int, or None when absent or unparseable. None never matches
        an unlock endpoint, so a DSN with no explicit port stays locked.
    """
    try:
        port = urlparse(url).port                              # None when absent; ValueError when junk
        if port is not None:
            return port
    except ValueError:
        return None
    for token in url.replace("\t", " ").split():
        if token.lower().startswith("port="):
            value = token[len("port="):].strip("'\"")
            return int(value) if value.isdigit() else None
    return None


def _unlock_side_channels(url: str) -> str | None:
    """Name the first way this DSN could reach a host OTHER than its parsed host.

    libpq lets one connection string carry several targets: comma-separated
    multi-host lists, repeated ``host=`` keywords (last wins), ``hostaddr=`` (an IP
    that overrides ``host``), and ``?host=``/``?port=`` query overrides. The guard
    compares only the parsed host, so an unlocked DSN must not carry any of these.

    Returns:
        A short description of the offending construct, or None if the DSN is a
        plain single-endpoint string.
    """
    lowered = url.lower()
    if "hostaddr" in lowered:
        return "a 'hostaddr' override"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "an unparseable URL"
    if "," in parsed.netloc:
        return "a comma-separated multi-host list"
    for key in parse_qs(parsed.query, keep_blank_values=True):   # ?host= / ?port= override the netloc
        if key.lower() in ("host", "port"):
            return f"a '?{key.lower()}=' query override"
    tokens = [t.lower() for t in url.replace("\t", " ").split()]
    if sum(t.startswith("host=") for t in tokens) > 1:
        return "more than one 'host=' keyword"
    return None


def assert_scratch_target(url: str, confirmed: bool) -> str:
    """Refuse to proceed unless the target is an explicitly-confirmed non-prod DB.

    Fails CLOSED: the blocked-marker check runs against the whole connection
    string (so URL *and* keyword-form DSNs are covered), and an indeterminate
    host is refused rather than assumed safe.

    The single exception is the ``host:port`` endpoint named in
    ``MIGRATION_UNLOCK_HOST`` (see :func:`_unlock_endpoint`): when the *parsed*
    host and port both equal it, the blocked-marker check skips that one endpoint.
    Everything else in the DSN is still scanned, and multi-target constructs
    (multi-host lists, ``hostaddr``, query overrides) are refused, so the
    connection can only reach the named endpoint. The confirmation flag stays
    mandatory for writing commands and a banner is printed, so targeting
    production is always a visible two-key act (the exact endpoint in the
    environment + the flag on the command line).

    Args:
        url: Target connection string (URL form or libpq keyword form).
        confirmed: Whether the caller passed the explicit confirmation flag.

    Returns:
        The lower-cased target host, for logging.

    Raises:
        MigrationConfigError: blocked host, undeterminable host, an unlocked DSN
            that could reach a second host, or missing confirmation.
    """
    host = (_extract_host(url) or "").lower()           # parse first: the unlock compares endpoints, never substrings
    port = _extract_port(url)
    unlock = _unlock_endpoint()                          # None unless MIGRATION_UNLOCK_HOST is set (and well-formed)
    unlocked = bool(host) and unlock is not None and (host, port) == unlock   # host AND port must match
    raw = url.lower()
    if unlocked:
        side_channel = _unlock_side_channels(url)        # refuse anything that could reach a second host
        if side_channel:
            raise MigrationConfigError(
                f"Refusing to run: the connection string carries {side_channel}, so "
                f"it could reach a host other than the unlocked {host}:{port}. Use a "
                "plain single-endpoint DSN."
            )
        raw = raw.replace(host, "")                      # mask the unlocked host, then scan what's left
        # Loud and on stderr, so it survives stdout redirection to a log file.
        print(
            f"!!! {UNLOCK_HOST_ENV} is set: TARGETING UNLOCKED ENDPOINT {host}:{port}. "
            "The production-host guard is bypassed for this endpoint. !!!",
            file=sys.stderr,
        )
    for marker in _blocked_markers():                    # a marker anywhere else in the DSN still refuses
        if marker and marker.lower() in raw:
            raise MigrationConfigError(
                f"Refusing to run: connection string matches blocked marker "
                f"{marker!r}. This looks like production. (To target it on "
                f"purpose, set {UNLOCK_HOST_ENV} to that exact host:port.)"
            )
    if not host:                                         # fail-closed: no host -> no unlock, no run
        raise MigrationConfigError(
            "Could not determine the target host from the connection string. "
            "Refusing (fail-closed) -- use an explicit host, e.g. "
            "postgresql://postgres:postgres@localhost:5432/vision_scratch."
        )
    if not confirmed:                                    # the flag is required even for an unlocked endpoint
        raise MigrationConfigError(
            f"Target host is {host!r}. Re-run with --i-understand-scratch-db to confirm "
            "this is the intended target database."
            + (" (The unlocked endpoint still requires the flag.)" if unlocked else "")
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
