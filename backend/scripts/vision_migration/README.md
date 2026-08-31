# Vision → Velocity migration tooling (local, Windows-only)

Local one-time cutover tooling. **Not** part of the deployed backend — it reads
the legacy UC Vision Access (`.mdb`) backend and loads it into Postgres.

See `docs/MIGRATION_PROPOSAL.md` for the full plan. This directory is **PKG 1**:
Stage A raw staging + a load-verification harness.

## What it does
- **`stage`** — copies every Vision *user* table verbatim into an isolated
  `vision_legacy` schema in Postgres (drop-and-recreate; safe to re-run).
- **`verify`** — compares each table's source row count (Access) against the
  staged count (Postgres) and reports mismatches.

It writes **only** to the `vision_legacy` schema; it never touches Velocity's
application tables.

## Safety
- Target DB is read from `MIGRATION_DATABASE_URL` (or `--database-url`) — a
  **separate** variable from the app's `DATABASE_URL`, so it can't inherit a
  prod-pointing shell.
- Writing commands (`stage`, `load-velocity`, `import`) refuse to run without
  `--i-understand-scratch-db`, and every command **hard-blocks** any connection
  string containing `railway.internal`, `rlwy.net`, or `railway.app` (Railway =
  production) — extend the denylist via `MIGRATION_BLOCKED_HOSTS` (comma-separated).
- **The one deliberate way through — `MIGRATION_UNLOCK_HOST`.** For the real
  cutover (or a dress rehearsal against a PR-preview database) set it to the
  **single exact endpoint, as `host:port`**, you intend to write to, e.g.
  `MIGRATION_UNLOCK_HOST=nozomi.proxy.rlwy.net:52032`. Only a DSN whose parsed
  host **and** port both equal that value bypasses the denylist for that one
  endpoint. The port is mandatory because Railway's public proxies are shared
  regional hostnames — several databases can sit behind one
  `<name>.proxy.rlwy.net` told apart only by port, so a hostname alone could mean
  production when you intended a preview. A substring such as `rlwy.net` unlocks
  nothing, every other host/port stays blocked, the rest of the DSN is still
  scanned for blocked markers, and multi-host / `hostaddr` / `?host=` forms are
  refused outright. The `--i-understand-scratch-db` flag is still required on top
  of it, and each run prints a `!!! TARGETING UNLOCKED ENDPOINT … !!!` banner.
  Unset the variable when done and the guard re-locks by itself.
- **Do not point this at production casually.** Default to a local or
  throwaway/preview Postgres; the unlock exists for the planned, backed-up cutover.

## Setup (Windows)
```powershell
# 1. A scratch Postgres. Simplest: a local DB (nothing leaves your machine).
#    e.g. create an empty database "vision_scratch" on localhost.

# 2. A venv for the tooling (keep it separate from the backend venv).
cd backend/scripts
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r vision_migration/requirements.txt
```

## Run
```powershell
$env:MIGRATION_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/vision_scratch"
$env:MIGRATION_MDB          = "C:\path\to\Dev_UCVision5_BE(2).mdb"
$env:MIGRATION_WORKGROUP    = "C:\Users\<you>\AppData\Roaming\Microsoft\Access\System.mdw"

python -m vision_migration stage --i-understand-scratch-db
python -m vision_migration verify
```

`verify` exits non-zero if any table's counts don't match.

## Notes / known-fiddly bits
- The Access read uses ADODB via `pywin32` with the proven workgroup-unlock
  connection string; it works while Access has the file open (`Share Deny None`),
  but keep Access **read-only** during a run.
- Date/time and binary/OLE values are coerced from COM types on load
  (see `stage_raw._coerce`). If an OLE/blob value can't be converted it is stored
  NULL **and counted** — `stage` prints a WARNING listing the affected columns,
  and `verify` compares per-column non-null counts, so any such loss shows up as a
  MISMATCH rather than passing silently. (Full photo handling is a later spike.)
- `verify` checks row counts **and** per-column non-null counts; it exits
  non-zero on any mismatch.
