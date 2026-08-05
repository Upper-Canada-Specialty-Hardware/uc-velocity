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
from . import reconcile
from . import stage_raw
from . import survey
from . import verify_staging


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
                         help="Confirm the target is a throwaway/preview DB, not production.")

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
    except config.MigrationConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
