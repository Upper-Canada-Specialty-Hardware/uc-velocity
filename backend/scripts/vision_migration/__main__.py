"""CLI entry point.

Run from `backend/scripts`:
    python -m vision_migration stage     --mdb "<path.mdb>" --workgroup "<path.mdw>" --i-understand-scratch-db
    python -m vision_migration verify    --mdb "<path.mdb>" --workgroup "<path.mdw>"
    python -m vision_migration reconcile

The target Postgres comes from --database-url or MIGRATION_DATABASE_URL.
See README.md for full setup.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import config
from . import load_velocity
from . import reconcile
from . import stage_raw
from . import survey
from . import verify_staging


# Every dry-run domain the CLI can preview. The quote domain's module is `dry_run`;
# every other domain lives in `<domain>_dry_run` with a `run_<domain>_dry_run(url)`.
_DRYRUN_DOMAINS = ["quote", "category", "part", "labor", "misc",
                   "customer", "vendor", "staff", "project", "po", "invoice"]


def _run_dryrun(domain: str, target: str) -> None:
    """Import and run one domain's read-only dry-run against the staging target."""
    import importlib
    mod_name = "dry_run" if domain == "quote" else f"{domain}_dry_run"   # quote's module is dry_run
    module = importlib.import_module(f"{__package__}.sync.{mod_name}")
    getattr(module, f"run_{domain}_dry_run")(target)                     # run_quote_dry_run(url), etc.


def _common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mdb", default=os.getenv("MIGRATION_MDB"),
                   help="Path to the Vision .mdb (or set MIGRATION_MDB).")
    p.add_argument("--workgroup", default=os.getenv("MIGRATION_WORKGROUP"),
                   help="Path to the workgroup .mdw (or set MIGRATION_WORKGROUP).")
    p.add_argument("--database-url", default=None,
                   help="Target Postgres URL (or set MIGRATION_DATABASE_URL).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vision_migration")
    sub = parser.add_subparsers(dest="command", required=True)

    p_stage = sub.add_parser("stage", help="Raw-stage all Vision tables into vision_legacy.")
    _common_args(p_stage)
    p_stage.add_argument("--i-understand-scratch-db", action="store_true",
                         help="Confirm the target DB is intended (required even for a host "
                              "unlocked via MIGRATION_UNLOCK_HOST).")

    p_verify = sub.add_parser("verify", help="Compare source vs staged row counts.")
    _common_args(p_verify)

    p_reconcile = sub.add_parser(
        "reconcile",
        help="Read-only parity report (Diff A): transform staged data and quantify gaps.",
    )
    p_reconcile.add_argument("--database-url", default=None,
                             help="Target Postgres URL (or set MIGRATION_DATABASE_URL).")

    p_survey = sub.add_parser(
        "survey",
        help="Read-only survey: staged tables, row counts, migrated vs. remaining.",
    )
    p_survey.add_argument("--database-url", default=None,
                          help="Target Postgres URL (or set MIGRATION_DATABASE_URL).")

    p_load = sub.add_parser(
        "load-velocity",
        help="Copy live Velocity (read-only) into velocity_current, for Diff B.",
    )
    p_load.add_argument("--database-url", default=None,
                        help="Target staging Postgres URL (or MIGRATION_DATABASE_URL).")
    p_load.add_argument("--velocity-source-url", default=None,
                        help="Live Velocity Postgres URL, read-only (or "
                             "MIGRATION_VELOCITY_SOURCE_URL); e.g. the Railway public URL.")
    p_load.add_argument("--i-understand-scratch-db", action="store_true",
                        help="Confirm the TARGET DB is intended (required even for a host "
                             "unlocked via MIGRATION_UNLOCK_HOST).")

    p_dryrun = sub.add_parser(
        "dryrun",
        help="Read-only: what the quote import would do (adopt/insert/update/skip) "
             "vs velocity_current.",
    )
    p_dryrun.add_argument("--database-url", default=None,
                          help="Staging Postgres URL (or set MIGRATION_DATABASE_URL).")
    p_dryrun.add_argument("--domain", choices=_DRYRUN_DOMAINS, default="quote",
                          help="Which import domain to dry-run (default: quote).")

    p_import = sub.add_parser(
        "import",
        help="WRITE path: FK-ordered import into a scratch target (adopt/insert/update, "
             "never delete). Previews (rolls back) unless --commit.",
    )
    p_import.add_argument("--database-url", default=None,
                          help="Target scratch Postgres URL (or set MIGRATION_DATABASE_URL).")
    p_import.add_argument("--commit", action="store_true",
                          help="Persist the run. Omit for a preview that rolls everything back.")
    p_import.add_argument("--i-understand-scratch-db", action="store_true",
                          help="Confirm the target DB is intended (required even for a host "
                               "unlocked via MIGRATION_UNLOCK_HOST).")
    p_import.add_argument("--domain", default=None,
                          help="Run only this one domain (its FK-parents must already be "
                               "imported). Omit to run all domains in dependency order.")

    args = parser.parse_args(argv)

    try:
        target = config.resolve_target_url(args.database_url)
        if args.command == "stage":
            host = config.assert_scratch_target(target, args.i_understand_scratch_db)
            print(f"Target host: {host} (staging into schema '{config.STAGING_SCHEMA}')")
            stage_raw.stage_all(args.mdb, args.workgroup, target)
            print("Staging complete.")
            return 0
        if args.command == "verify":
            # verify only reads; still refuse blocked hosts, but no confirm needed.
            config.assert_scratch_target(target, confirmed=True)
            ok = verify_staging.verify(args.mdb, args.workgroup, target)
            return 0 if ok else 1
        if args.command == "reconcile":
            # reconcile only reads staged data; refuse blocked hosts, no confirm needed.
            config.assert_scratch_target(target, confirmed=True)
            reconcile.run_reconciliation(target)
            return 0
        if args.command == "survey":
            # survey only reads staged data; refuse blocked hosts, no confirm needed.
            config.assert_scratch_target(target, confirmed=True)
            survey.run_survey(target)
            return 0
        if args.command == "load-velocity":
            # Writes into velocity_current on the TARGET -> guard the target like
            # 'stage'. The SOURCE (live Velocity) is read-only and NOT guarded.
            host = config.assert_scratch_target(target, args.i_understand_scratch_db)
            source = config.resolve_velocity_source_url(args.velocity_source_url)
            print(f"Target host: {host} (loading into schema "
                  f"'{config.VELOCITY_CURRENT_SCHEMA}')")
            print("Source: live Velocity (opened read-only).")
            load_velocity.load_velocity(source, target)
            print("Velocity load complete. Run 'reconcile' to see Diff B.")
            return 0
        if args.command == "dryrun":
            # Read-only: plans the chosen domain's import against velocity_current and
            # writes nothing. Refuse blocked (prod-looking) hosts; no confirm needed.
            config.assert_scratch_target(target, confirmed=True)
            _run_dryrun(args.domain, target)
            return 0
        if args.command == "import":
            # WRITE path: performs the FK-ordered import into the scratch target. The
            # driver guards the target (prod-host refused) and needs
            # --i-understand-scratch-db; without --commit it previews (rolls back). It
            # never deletes.
            from .sync import import_all
            import_all.run_import(target, commit=args.commit,
                                  confirmed=args.i_understand_scratch_db, only=args.domain)
            return 0
    except config.MigrationConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
