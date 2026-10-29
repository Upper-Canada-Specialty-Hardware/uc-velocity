"""Send a measured cutover plan to the admin API, batch by batch.

Run from ``backend/`` after plan.py, in your own shell (it asks for the
cutover key at a masked prompt)::

    python -m scripts.vision_delta.push --plan <out>/plan.json --api https://<backend host> --out <folder>
                                        [--operator "<your name>"] [--only-preflight]

It opens (or resumes) the run for the plan, records what the plan leaves as it
is, then sends each batch. When the server's pre-flight check says wait (409:
too much WAL since the last checkpoint, the archiver behind, or the disk limit
near), it waits and sends the same batch again; batches already applied are
answered as repeats, so the script can simply be run again after any stop.
Each batch's change log is kept under ``<out>/log`` (the bucket holds the
server's copy), for verify.py.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

WAIT = 30                                                  # seconds between pre-flight retries
MAX_WAIT = 30 * 60                                         # give up on one batch after this long
RETRIES = 3                                                # network errors / 5xx per request


def call(client, method: str, path: str, body=None):
    """One API request with retries on network errors and 5xx (every route is safe to repeat).

    Args:
        client: An ``httpx.Client`` with the base URL and the key header.
        method: ``"GET"`` or ``"POST"``.
        path: Route under ``/admin/vision-cutover``.
        body: Data to send as JSON (encoded with runner.encode).

    Returns:
        The ``httpx.Response`` (any status below 500, or the last 5xx).
    """
    import httpx
    from .runner import encode
    content = json.dumps(encode(body)).encode("utf-8") if body is not None else None
    for attempt in range(1, RETRIES + 1):
        try:
            r = client.request(method, f"/admin/vision-cutover{path}", content=content,
                               headers={"Content-Type": "application/json"} if content else None)
            if r.status_code < 500 or attempt == RETRIES:
                return r
            print(f"    server {r.status_code}: {r.text[:200]} (retry {attempt})")
        except httpx.TransportError as e:
            if attempt == RETRIES:
                raise
            print(f"    network: {e} (retry {attempt})")
        time.sleep(5 * attempt)


def ok(r, what: str):
    """The decoded body of a successful response.

    Raises:
        SystemExit: Any other status, with the server's reason.
    """
    from .runner import decode
    if r.status_code != 200:
        raise SystemExit(f"{what}: {r.status_code} {r.text[:500]}")
    return decode(r.content)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and push the plan.

    Args:
        argv: Command-line arguments (defaults to ``sys.argv``).

    Returns:
        Process exit code (1 when any record failed).
    """
    import httpx
    from log_store import FolderStore
    from .apply import log_rows
    from .runner import decode, log_key
    ap = argparse.ArgumentParser(prog="python -m scripts.vision_delta.push", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="plan.json from plan.py")
    ap.add_argument("--api", required=True, help="the backend's base URL (https://...)")
    ap.add_argument("--out", required=True, help="folder for the local copy of the change log")
    ap.add_argument("--operator", default=getpass.getuser(), help="your name, shown on the run")
    ap.add_argument("--only-preflight", action="store_true", help="show the pre-flight figures and stop")
    args = ap.parse_args(argv)

    with open(args.plan, encoding="utf-8") as f:
        plan = decode(f.read())
    meta, batches, left = plan["meta"], plan["batches"], plan["left"]
    key = os.getenv("VISION_CUTOVER_KEY") or getpass.getpass("cutover key (VISION_CUTOVER_KEY on the backend): ")
    store = FolderStore(os.path.join(args.out, "log"))
    client = httpx.Client(base_url=args.api.rstrip("/"), headers={"X-Cutover-Key": key}, timeout=300)

    pre = ok(call(client, "GET", "/preflight"), "pre-flight")
    print(f"plan {meta['hash'][:12]}: {len(batches)} batches, measured WAL {meta.get('wal_mb')} MB, "
          f"growth {meta.get('growth_mb')} MB\nserver now: {pre['now']}\nlimits: {pre['limits']}")
    if args.only_preflight:
        return 0

    run = ok(call(client, "POST", "/runs", {"meta": meta, "operator": args.operator}), "open run")
    run_id = run["run_id"]
    print(f"run {run_id} ({'new' if run['created'] else 'resumed'})")
    from .plan import LEFT_PART
    for part, i in enumerate(range(0, len(left), LEFT_PART), 1):
        rows = left[i:i + LEFT_PART]
        res = ok(call(client, "POST", f"/runs/{run_id}/left/{part}", rows), f"left part {part}")
        store.put_json(log_key(run_id, f"left-{part:04d}"), log_rows(rows))   # what the server logged, locally
        print(f"  left part {part}: {res['rows']} rows, {res['in_db']} for a person{' (repeat)' if res.get('repeat') else ''}")

    failed = 0
    for n, b in enumerate(batches, 1):
        waited = 0
        while True:
            r = call(client, "POST", f"/runs/{run_id}/batches", b)
            if r.status_code != 409:
                break
            detail = r.json().get("detail")
            if waited >= MAX_WAIT or not isinstance(detail, dict):
                raise SystemExit(f"batch {b['key']}: {r.text[:500]}")
            print(f"  batch {b['key']}: wait ({'; '.join(detail['wait'])})")
            time.sleep(WAIT)
            waited += WAIT
        res = ok(r, f"batch {b['key']}")
        result = res["result"]
        name = f"batch-{b['key']}"
        if res["entries"]:
            store.put_json(log_key(run_id, name), res["entries"])   # local copy for verify.py
        else:
            try:
                store.get_json(log_key(run_id, name))
            except KeyError:                               # applied earlier, its answer lost: fetch the server's log
                store.put_json(log_key(run_id, name), ok(call(client, "GET", f"/runs/{run_id}/log/{name}"), name))
        failed += result.get("actions", {}).get("failed", 0)
        print(f"  batch {b['key']} ({n}/{len(batches)}): {result.get('actions')}{' (repeat)' if result.get('repeat') else ''}")

    totals = ok(call(client, "POST", f"/runs/{run_id}/finish"), "finish")
    print(f"\nrun {run_id} finished: {totals.get('actions')}\n"
          f"next: python -m scripts.vision_delta.verify --csv <export> --target <url> --run {run_id} "
          f"--log-dir {os.path.join(args.out, 'log')} --out <folder>")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
