"""Command line: compare a Vision CSV export with a Velocity database, read-only.

Run from ``backend/``::

    python -m scripts.vision_delta --csv <export folder> --target <postgres url> [--out <folder>]
                                   [--june-cutoff YYYY-MM-DD] [--projects-since YYYY-MM-DD]

``--target`` may also come from ``VISION_DELTA_TARGET_URL``. The Velocity
connection is read-only, so production may be the target. Nothing is written
anywhere except the output folder.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from urllib.parse import urlparse


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the compare, write the outputs.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv``).

    Returns:
        Process exit code.
    """
    ap = argparse.ArgumentParser(prog="python -m scripts.vision_delta", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="folder holding the Vision CSV export (tblX.csv)")
    ap.add_argument("--target", default=os.getenv("VISION_DELTA_TARGET_URL"), help="Velocity Postgres URL")
    ap.add_argument("--out", default="vision_delta_out", help="output folder (default: vision_delta_out)")
    ap.add_argument("--june-cutoff", type=date.fromisoformat, help="day of the June import (default: estimated)")
    ap.add_argument("--projects-since", type=date.fromisoformat, default=date(2025, 1, 1),
                    help="first creation day for the per-project review sheet (default: 2025-01-01)")
    args = ap.parse_args(argv)
    if not args.target:
        ap.error("--target (or VISION_DELTA_TARGET_URL) is required")

    # The app modules read DATABASE_URL at import; point it at the target (only read from)
    os.environ.setdefault("DATABASE_URL", args.target)
    from . import compare, velocity, vision, workbook

    started = time.time()
    host = urlparse(args.target).hostname
    print(f"vision export: {args.csv}\nvelocity:      {host} (read-only)")
    v = vision.load(args.csv)
    print(f"read vision:   {len(v['quotes'])} quotes, {len(v['pos'])} POs")
    conn = velocity.connect(args.target)
    try:
        t = velocity.load(conn)
    finally:
        conn.close()
    print(f"read velocity: {len(t['quotes'])} quotes, {len(t['pos'])} POs")

    cutoff = args.june_cutoff or t["recorded_cutoff"] or compare.june_cutoff(t["quotes"], t["first_snapshot"])
    source = "given" if args.june_cutoff else "recorded by an earlier run" if t["recorded_cutoff"] else "estimated"
    if cutoff is None:
        print("no imported quotes found; pass --june-cutoff", file=sys.stderr)
        return 2
    print(f"june import:   {cutoff} ({source})")

    delta = compare.run(v, t, cutoff)
    review = compare.project_review(delta, v, t, args.projects_since)   # one row per recent project
    meta = {"vision export": os.path.abspath(args.csv), "velocity host": host, "june import day": cutoff,
            "cutoff source": source, "run at": datetime.now().replace(microsecond=0),
            "projects reviewed": f"{len(review)} created since {args.projects_since}"}
    xlsx, js = workbook.write(delta, args.out, meta, review, args.projects_since)

    print(f"\n{'level':24}" + "".join(f"{k:>10}" for k in workbook.SUMMARY_COLUMNS))
    for level, c in delta.counts.items():
        print(f"{level:24}" + "".join(f"{c.get(k, 0):>10}" for k in workbook.SUMMARY_COLUMNS))
    print(f"\n{len(delta.rows)} difference rows -> {xlsx}\n{' ' * 20}{js}\n({time.time() - started:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
