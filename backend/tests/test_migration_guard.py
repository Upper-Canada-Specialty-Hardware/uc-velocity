"""Unit tests for the migration tooling's production-host guard.

Covers ``config.assert_scratch_target`` and the deliberate ``MIGRATION_UNLOCK_HOST``
exception. DB-free: the guard only inspects connection strings and environment
variables, so these run everywhere (CI's Backend job runs them with the rest of
the suite).
"""
import pytest

from scripts.vision_migration import config
from scripts.vision_migration.config import MigrationConfigError, assert_scratch_target

# A realistic Railway public-proxy endpoint (the shape a production URL has).
# Railway proxies are SHARED regional hostnames; databases differ by port.
PROD_HOST = "nozomi.proxy.rlwy.net"
PROD_PORT = 52032
PROD_ENDPOINT = f"{PROD_HOST}:{PROD_PORT}"
PROD_URL = f"postgresql://postgres:pw@{PROD_ENDPOINT}/railway"
# Same server expressed in libpq keyword form (also accepted by psycopg2).
PROD_KEYWORD_DSN = f"host={PROD_HOST} port={PROD_PORT} dbname=railway user=postgres password=pw"
# A different database on the SAME proxy hostname (only the port differs).
OTHER_DB_SAME_HOST_URL = f"postgresql://postgres:pw@{PROD_HOST}:20674/railway"
SCRATCH_URL = "postgresql://postgres:postgres@localhost:5433/vision_scratch"


@pytest.fixture(autouse=True)
def _locked_by_default(monkeypatch):
    """Start every test fully locked: no unlock endpoint, no extra blocked markers."""
    monkeypatch.delenv(config.UNLOCK_HOST_ENV, raising=False)
    monkeypatch.delenv("MIGRATION_BLOCKED_HOSTS", raising=False)


# --------------------------------------------------------------------------- #
# Locked (default) behaviour -- must be unchanged by the unlock feature
# --------------------------------------------------------------------------- #

def test_railway_url_refused_even_when_confirmed():
    """The flag alone can never open a production-looking host."""
    with pytest.raises(MigrationConfigError, match="blocked marker"):
        assert_scratch_target(PROD_URL, confirmed=True)


def test_railway_keyword_dsn_refused():
    """Keyword-form DSNs are checked too (the marker scan covers the whole string)."""
    with pytest.raises(MigrationConfigError, match="blocked marker"):
        assert_scratch_target(PROD_KEYWORD_DSN, confirmed=True)


def test_refusal_message_names_the_unlock_env():
    """The refusal tells the operator the deliberate path exists."""
    with pytest.raises(MigrationConfigError, match=config.UNLOCK_HOST_ENV):
        assert_scratch_target(PROD_URL, confirmed=True)


def test_scratch_host_requires_confirmation():
    with pytest.raises(MigrationConfigError, match="--i-understand-scratch-db"):
        assert_scratch_target(SCRATCH_URL, confirmed=False)


def test_scratch_host_passes_when_confirmed():
    assert assert_scratch_target(SCRATCH_URL, confirmed=True) == "localhost"


def test_missing_host_fails_closed():
    with pytest.raises(MigrationConfigError, match="fail-closed"):
        assert_scratch_target("dbname=railway user=postgres", confirmed=True)


def test_malformed_url_is_a_clean_refusal_not_a_traceback():
    """An unbalanced '[' used to raise urlparse's ValueError; now it's a guard refusal."""
    with pytest.raises(MigrationConfigError):
        assert_scratch_target("postgresql://u:p@[localhost:1/db", confirmed=True)


def test_extra_blocked_markers_env_still_respected(monkeypatch):
    """MIGRATION_BLOCKED_HOSTS can still ADD markers (it never removes any)."""
    monkeypatch.setenv("MIGRATION_BLOCKED_HOSTS", "my-staging-box")
    with pytest.raises(MigrationConfigError, match="my-staging-box"):
        assert_scratch_target("postgresql://u:p@my-staging-box:5432/db", confirmed=True)


# --------------------------------------------------------------------------- #
# Unlocked behaviour -- the deliberate two-key path
# --------------------------------------------------------------------------- #

def test_exact_endpoint_unlock_passes_with_flag(monkeypatch, capsys):
    """Exact host:port in the env + the flag = allowed, and a banner is printed."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_ENDPOINT)
    assert assert_scratch_target(PROD_URL, confirmed=True) == PROD_HOST
    assert "TARGETING UNLOCKED ENDPOINT" in capsys.readouterr().err


def test_unlock_is_case_insensitive_on_host(monkeypatch):
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_ENDPOINT.upper())
    assert assert_scratch_target(PROD_URL, confirmed=True) == PROD_HOST


def test_unlock_keyword_dsn_passes(monkeypatch):
    """The unlock compares the *parsed* host and port, so keyword-form DSNs work too."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_ENDPOINT)
    assert assert_scratch_target(PROD_KEYWORD_DSN, confirmed=True) == PROD_HOST


def test_unlocked_endpoint_still_requires_flag(monkeypatch):
    """The env var alone is one key; the CLI flag is the second. Both are needed."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_ENDPOINT)
    with pytest.raises(MigrationConfigError, match="still requires the flag"):
        assert_scratch_target(PROD_URL, confirmed=False)


def test_same_host_different_port_stays_locked(monkeypatch):
    """Railway shares proxy hostnames: unlocking one port must not open another DB."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, f"{PROD_HOST}:20674")   # a preview on the same proxy
    with pytest.raises(MigrationConfigError, match="blocked marker"):
        assert_scratch_target(PROD_URL, confirmed=True)               # prod (port 52032) refused


def test_dsn_without_a_port_never_matches_an_unlock(monkeypatch):
    """No explicit port in the DSN -> port is None -> can't equal the unlock -> locked."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, f"{PROD_HOST}:5432")
    with pytest.raises(MigrationConfigError, match="blocked marker"):
        assert_scratch_target(f"postgresql://u:p@{PROD_HOST}/railway", confirmed=True)


def test_unlock_of_a_different_host_does_not_open_prod(monkeypatch):
    """Unlocking a preview endpoint must not loosen the guard for any other host."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, "sakura.proxy.rlwy.net:40955")
    with pytest.raises(MigrationConfigError, match="blocked marker"):
        assert_scratch_target(PROD_URL, confirmed=True)


def test_unlock_value_without_port_is_refused_loudly(monkeypatch):
    """A hostname-only unlock is malformed: refuse and explain, don't silently ignore."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_HOST)
    with pytest.raises(MigrationConfigError, match="host:port"):
        assert_scratch_target(PROD_URL, confirmed=True)


def test_unlock_substring_is_malformed_not_a_wildcard(monkeypatch):
    """'rlwy.net' (no port) is neither an endpoint nor a wildcard -> refused."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, "rlwy.net")
    with pytest.raises(MigrationConfigError):
        assert_scratch_target(PROD_URL, confirmed=True)


def test_blank_unlock_env_is_ignored(monkeypatch):
    """Whitespace-only value == unset: fully locked."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, "   ")
    with pytest.raises(MigrationConfigError, match="blocked marker"):
        assert_scratch_target(PROD_URL, confirmed=True)


def test_unlock_does_not_bypass_missing_host(monkeypatch):
    """No parseable host -> no unlock possible, still fail-closed."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_ENDPOINT)
    with pytest.raises(MigrationConfigError, match="fail-closed"):
        assert_scratch_target("dbname=railway user=postgres", confirmed=True)


def test_unlock_does_not_affect_scratch_targets(monkeypatch, capsys):
    """With an unlock set for prod, a normal scratch URL behaves exactly as before."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_ENDPOINT)
    assert assert_scratch_target(SCRATCH_URL, confirmed=True) == "localhost"
    assert "UNLOCKED" not in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Unlocked, but the DSN could reach a SECOND host -- must still be refused
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("dsn, why", [
    (f"postgresql://u:p@{PROD_ENDPOINT},other.proxy.rlwy.net:2/railway", "multi-host"),
    (f"postgresql://u:p@{PROD_ENDPOINT}/railway?host=other.proxy.rlwy.net", "?host= override"),
    (f"postgresql://u:p@{PROD_ENDPOINT}/railway?port=20674", "?port= override"),
    (f"host={PROD_HOST} port={PROD_PORT} host=other.proxy.rlwy.net dbname=x", "repeated host="),
    (f"host={PROD_HOST} port={PROD_PORT} hostaddr=203.0.113.9 dbname=x", "hostaddr"),
])
def test_unlocked_dsn_with_a_second_target_is_refused(monkeypatch, dsn, why):
    """libpq can be steered to another host by these forms; the unlock must not allow them."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_ENDPOINT)
    with pytest.raises(MigrationConfigError):
        assert_scratch_target(dsn, confirmed=True)


def test_unlocked_dsn_still_scans_the_rest_for_markers(monkeypatch):
    """Masking removes only the unlocked host; any other blocked marker still refuses."""
    monkeypatch.setenv(config.UNLOCK_HOST_ENV, PROD_ENDPOINT)
    # A second blocked host smuggled into the password field (no side-channel construct).
    dsn = f"postgresql://u:secret.railway.internal@{PROD_ENDPOINT}/railway"
    with pytest.raises(MigrationConfigError, match="blocked marker"):
        assert_scratch_target(dsn, confirmed=True)
