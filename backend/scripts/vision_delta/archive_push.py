"""Send a built archive to the backend, then (separately) remove the archived rows.

Run from ``backend/`` in your own shell. The cutover key comes from
``VISION_CUTOVER_KEY`` or a masked prompt::

    python -m scripts.vision_delta.archive_push build  --csv <export> --api https://<backend host> --out <folder>
    python -m scripts.vision_delta.archive_push files  --dir <folder> --api https://<backend host>
    python -m scripts.vision_delta.archive_push remove --dir <folder> --api https://<backend host> --operator "<name>"

``build`` reads the Vision export here, picks the projects with no activity
since ``--active-since`` (2018-01-01), and sends them to the server a few per
request; the server reads Velocity's rows, builds each file and writes it to
the bucket, then ``build`` writes the index and manifest. It saves a copy of
the manifest, index and the kept list into ``--out`` so ``remove`` can run from
there. No production database URL is needed on the client. This is the
counterpart of ``archive.py``'s local-folder build, which reads a database.

``files`` writes a locally built folder's project files to the bucket (the
server reads each one back), then the index, and the manifest last: until the
manifest is there, nothing can be removed. Running ``build`` or ``files`` again
sends everything again (same files).

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
PROJECTS_PER_BUILD = 25                                    # projects built per request (server caps at 50)
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


def build(client, csv: str, out: str, active_since: str, june_day: str) -> int:
    """Pick the old projects here and have the server build and store their files.

    Reads the Vision export locally, sends the projects with no Vision activity
    since ``active_since`` to ``/archive/build`` a few per request, collects the
    index and manifest entries the server returns, then calls ``/archive/finish``
    to write the index and manifest. Saves a copy of the manifest, index and the
    kept list into ``out`` so ``remove`` can run from there. No database URL here.

    Args:
        client: An ``httpx`` client carrying the cutover key.
        csv: The Vision export folder.
        out: A local folder for the manifest, index and kept copies (must be new).
        active_since: Projects with Vision activity on or after this day stay.
        june_day: First day after the June import (passed through to the server).

    Returns:
        0 on success (raises :class:`SystemExit` on a server error).
    """
    import json
    from datetime import date, datetime
    from log_store import FolderStore
    # vision.load reads the export files only and never opens a connection, but importing it pulls in
    # routes.migration -> database, which needs DATABASE_URL set at import time. A never-connected
    # placeholder keeps the client free of any real (let alone production) database URL.
    os.environ.setdefault("DATABASE_URL", "postgresql://unused@localhost/unused")
    from . import vision
    from .archive import vision_version
    from .plan import last_activity
    if os.path.exists(os.path.join(out, PREFIX, "manifest.json")):
        raise SystemExit(f"{out} already holds an archive; use a new folder")
    since = date.fromisoformat(active_since)
    v = vision.load(csv)                                   # the Vision export (projects, work orders, POs)
    act = last_activity(v, {})                             # newest Vision date per Vision number
    old = sorted(u for u, d in act.items() if d < since)   # the projects with nothing since active_since
    index, files, kept = [], {}, {}                        # index entries, manifest entries, kept-project reasons
    for i in range(0, len(old), PROJECTS_PER_BUILD):
        part = old[i:i + PROJECTS_PER_BUILD]
        payload = {"active_since": active_since, "june_day": june_day,
                   "projects": [{"uca": u, "last_activity": act[u], "vision": vision_version(v, u)} for u in part]}
        payload = json.loads(json.dumps(payload, default=str))   # dates to strings, as the stored files hold them
        r = _send(client, "POST", "/archive/build", payload)
        if r.status_code != 200:
            raise SystemExit(f"build {i + 1}-{i + len(part)}: {r.status_code} {r.text[:500]}")
        for uca, res in r.json()["projects"].items():
            if "kept" in res:                              # touched since June, or an ambiguous number: left in place
                kept[uca] = res["kept"]
            else:
                files[res["key"]] = {"sha256": res["sha256"], "velocity_project_id": res["velocity_project_id"],
                                     "rows": res["rows"]}   # the manifest entry the server computed
                index.append(res["index"])                 # the archived-projects page row
        print(f"  {i + len(part)} of {len(old)} old projects: {len(files)} archived, {len(kept)} kept")
    index.sort(key=lambda row: str(row.get("uca") or ""))  # a stable order for the page
    meta = {"made_at": datetime.utcnow().isoformat(sep=" "), "active_since": active_since, "june_day": june_day,
            "projects": len(files), "in_velocity": sum(1 for m in files.values() if m["velocity_project_id"])}
    r = _send(client, "POST", "/archive/finish", {"meta": meta, "index": index, "manifest": files})
    if r.status_code != 200:
        raise SystemExit(f"finish: {r.status_code} {r.text[:500]}")
    store = FolderStore(out)                               # keep the manifest and index copies for `remove`
    store.put_json(f"{PREFIX}/manifest.json", {"meta": meta, "files": files})
    store.put_json(f"{PREFIX}/index.json", {"meta": meta, "projects": index})
    with open(os.path.join(out, "kept.json"), "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=1)
    for u, why in sorted(kept.items())[:20]:
        print(f"  kept: {u}: {', '.join(why)}")
    print(f"archived {len(files)} projects ({meta['in_velocity']} in velocity); {len(kept)} kept; folder {out}")
    return 0


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
    """Parse arguments and run ``build``, ``files`` or ``remove``."""
    import httpx
    p = argparse.ArgumentParser(description="build the archive through the backend, send a built folder, or remove rows")
    p.add_argument("step", choices=["build", "files", "remove"])
    p.add_argument("--api", required=True, help="the backend's base URL")
    p.add_argument("--dir", help="the folder archive.py (or a build) wrote (files, remove)")
    p.add_argument("--csv", help="the Vision export folder (build)")
    p.add_argument("--out", help="local folder for the manifest/index/kept copies (build)")
    p.add_argument("--active-since", default="2018-01-01", help="projects with Vision activity on or after this stay (build)")
    p.add_argument("--june-day", default="2026-06-19", help="first day after the June import (build)")
    p.add_argument("--operator", default="", help="who runs the removal (recorded with each project)")
    a = p.parse_args(argv)
    key = os.getenv("VISION_CUTOVER_KEY") or getpass.getpass("cutover key: ")
    with httpx.Client(base_url=a.api.rstrip("/"), headers={"X-Cutover-Key": key}, timeout=300) as client:
        if a.step == "build":
            if not a.csv or not a.out:
                raise SystemExit("--csv and --out are required for build")
            return build(client, a.csv, a.out, a.active_since, a.june_day)
        if not a.dir:
            raise SystemExit(f"--dir is required for {a.step}")
        if a.step == "files":
            return push_files(client, a.dir)
        if not a.operator:
            raise SystemExit("--operator is required for remove")
        return remove(client, a.dir, a.operator)


if __name__ == "__main__":
    sys.exit(main())
