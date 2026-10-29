"""Send a built archive to the backend, then (separately) remove the archived rows.

Run from ``backend/`` in your own shell, after ``archive.py`` built the folder.
The cutover key comes from ``VISION_CUTOVER_KEY`` or a masked prompt::

    python -m scripts.vision_delta.archive_push files  --dir <folder> --api https://<backend host>
    python -m scripts.vision_delta.archive_push remove --dir <folder> --api https://<backend host> --operator "<name>"

``files`` writes every project file to the bucket (the server reads each one
back), then the index, and the manifest last: until the manifest is there,
nothing can be removed. Running it again sends everything again (same files).

``remove`` sends the projects that are in Velocity, a few per request. The
server checks each one against its archive file and removes it only when its
rows are still exactly the archived ones; when its pre-flight check says wait
(WAL, archiver, disk), this waits and sends the same request again. Projects
already removed are answered as done, so it can simply be run again. The
results go to ``<folder>/removed.json``.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

from .archive import PREFIX

WAIT = 30                                                  # seconds between pre-flight retries
MAX_WAIT = 30 * 60                                         # give up on one request after this long
RETRIES = 3                                                # network errors / 5xx per request
FILES_PER_PUT = 50
PROJECTS_PER_REMOVE = 25
WAL_PER_ROW = 2000                                         # bytes of WAL per deleted row, generous (measured ~600 locally)


def _send(client, method: str, path: str, body: dict):
    """One request with retries on network errors and 5xx (both routes are safe to repeat)."""
    import httpx
    for attempt in range(1, RETRIES + 1):
        try:
            r = client.request(method, f"/admin/vision-cutover{path}", json=body)
            if r.status_code < 500 or attempt == RETRIES:
                return r
            print(f"    server {r.status_code}: {r.text[:200]} (retry {attempt})")
        except httpx.TransportError as e:
            if attempt == RETRIES:
                raise
            print(f"    network: {e} (retry {attempt})")
        time.sleep(5 * attempt)


def _read(root: str, key: str):
    """A file of the local archive folder."""
    with open(os.path.join(root, *key.split("/")), encoding="utf-8") as f:
        return json.load(f)


def push_files(client, root: str) -> int:
    """Send every project file, then the index, then the manifest."""
    manifest = _read(root, f"{PREFIX}/manifest.json")
    keys = sorted(manifest["files"])
    for i in range(0, len(keys), FILES_PER_PUT):
        part = keys[i:i + FILES_PER_PUT]
        r = _send(client, "PUT", "/archive/files", {"files": {k: _read(root, k) for k in part}})
        if r.status_code != 200:
            raise SystemExit(f"files {i + 1}-{i + len(part)}: {r.status_code} {r.text[:500]}")
        bad = [k for k in part if r.json()["written"].get(k) != manifest["files"][k]["sha256"]]
        if bad:
            raise SystemExit(f"read back differs for {bad[:5]}")
        print(f"  {i + len(part)} of {len(keys)} files written and read back")
    for key in (f"{PREFIX}/index.json", f"{PREFIX}/manifest.json"):   # the manifest last
        r = _send(client, "PUT", "/archive/files", {"files": {key: _read(root, key)}})
        if r.status_code != 200:
            raise SystemExit(f"{key}: {r.status_code} {r.text[:500]}")
        print(f"  {key} written")
    return 0


def remove(client, root: str, operator: str) -> int:
    """Remove the archived projects that are in Velocity, a few per request."""
    manifest = _read(root, f"{PREFIX}/manifest.json")
    june_day = manifest["meta"]["june_day"]
    todo = [(k, m) for k, m in sorted(manifest["files"].items()) if m["velocity_project_id"]]
    removed, refused = {}, {}
    for i in range(0, len(todo), PROJECTS_PER_REMOVE):
        part = todo[i:i + PROJECTS_PER_REMOVE]
        rows = sum(len(ids) for _, m in part for ids in m["rows"].values())
        body = {"keys": [k for k, _ in part], "operator": operator, "june_day": june_day,
                "estimate": {"wal_bytes": rows * WAL_PER_ROW, "db_bytes": 0}}
        waited = 0
        while True:
            r = _send(client, "POST", "/archive/remove", body)
            if r.status_code != 409:
                break
            if waited >= MAX_WAIT:
                raise SystemExit(f"waited {MAX_WAIT // 60} minutes for room: {r.text[:300]}; run again later")
            print(f"    wait: {r.json().get('detail', {}).get('wait')}")
            time.sleep(WAIT)
            waited += WAIT
        if r.status_code != 200:
            raise SystemExit(f"projects {i + 1}-{i + len(part)}: {r.status_code} {r.text[:500]}")
        out = r.json()
        removed.update(out["removed"])
        refused.update(out["refused"])
        now = out.get("now", {})
        print(f"  {i + len(part)} of {len(todo)}: {len(removed)} removed, {len(refused)} left "
              f"(db {now.get('db_mb')} MB, WAL since checkpoint {now.get('wal_since_checkpoint_mb')} MB)")
    with open(os.path.join(root, "removed.json"), "w", encoding="utf-8") as f:
        json.dump({"removed": removed, "refused": refused}, f, indent=1)
    for k, why in list(refused.items())[:20]:
        print(f"  left: {k}: {why}")
    print(f"removed {len(removed)} projects; {len(refused)} left as they are (see {root}\\removed.json)")
    return 1 if refused else 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run ``files`` or ``remove``."""
    import httpx
    p = argparse.ArgumentParser(description="send the archive to the backend, or remove archived rows")
    p.add_argument("step", choices=["files", "remove"])
    p.add_argument("--dir", required=True, help="the folder archive.py built")
    p.add_argument("--api", required=True, help="the backend's base URL")
    p.add_argument("--operator", default="", help="who runs the removal (recorded with each project)")
    a = p.parse_args(argv)
    key = os.getenv("VISION_CUTOVER_KEY") or getpass.getpass("cutover key: ")
    with httpx.Client(base_url=a.api.rstrip("/"), headers={"X-Cutover-Key": key}, timeout=300) as client:
        if a.step == "files":
            return push_files(client, a.dir)
        if not a.operator:
            raise SystemExit("--operator is required for remove")
        return remove(client, a.dir, a.operator)


if __name__ == "__main__":
    sys.exit(main())
