"""Tests for the archive of projects with no activity since 2018 (#304).

Run on a scratch database (CI's, or a local one through DATABASE_URL); each
test makes its own project and removes what is left of it afterwards. Covers
what counts as touched, that removal deletes exactly the archived rows and
refuses a project that changed, and the admin routes with a folder store.
"""
import os
from datetime import date, datetime

import pytest

JUNE = date(2026, 6, 19)


@pytest.fixture
def old_project(monkeypatch, tmp_path):
    """An old, June-imported project with one quote and two lines, plus a folder store and a cutover key."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL (CI, or a local scratch database)")
    from database import SessionLocal
    from models import Profile, ProfileType, Project, Quote, QuoteLineItem
    monkeypatch.setenv("LOG_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("VISION_CUTOVER_KEY", "test-key")
    monkeypatch.delenv("BUCKET", raising=False)
    db = SessionLocal()
    tag = datetime.utcnow().strftime("%H%M%S%f")
    cust = Profile(name=f"[TEST] archive {tag}", type=ProfileType.customer, pst="PST-TEST", address="1 Test St",
                   postal_code="A1A1A1")
    db.add(cust)
    db.flush()
    proj = Project(name=f"[TEST] archive {tag}", customer_id=cust.id, uca_project_number=f"TA{tag}",
                   created_on=datetime(2010, 1, 1))
    db.add(proj)
    db.flush()
    q = Quote(project_id=proj.id, quote_sequence=1, status="Closed", current_version=0, created_at=datetime(2010, 2, 1))
    db.add(q)
    db.flush()
    db.add_all([QuoteLineItem(quote_id=q.id, item_type="misc", description=d, quantity=1, unit_price=5,
                              qty_fulfilled=1, qty_pending=0) for d in ("a", "b")])
    db.commit()
    pid, qid, cid = proj.id, q.id, cust.id                 # plain ids: a test may delete these rows, expiring the ORM objects
    yield {"db": db, "project": proj, "quote": q, "customer": cust, "project_id": pid, "quote_id": qid}
    db.rollback()
    from models import Invoice
    db.query(Invoice).filter(Invoice.quote_id == qid).delete()           # by captured id, not a (possibly gone) object
    db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == qid).delete()
    db.query(Quote).filter(Quote.project_id == pid).delete()
    db.query(Project).filter(Project.id == pid).delete()
    db.query(Profile).filter(Profile.id == cid).delete()
    db.commit()
    db.close()


def _file(w):
    """The project's archive file as archive.py builds it."""
    from scripts.vision_delta import archive as ar
    pick = {"uca": w["project"].uca_project_number, "velocity_id": w["project"].id, "last_activity": date(2010, 2, 1)}
    v = {"projects": [], "quotes": [], "pos": []}
    import json
    return json.loads(json.dumps(ar.build_file(v, w["db"], pick), default=str))


def test_untouched_old_project_is_not_touched_and_a_person_edit_or_invoice_is(old_project):
    from scripts.vision_delta.archive import touched_projects
    from models import Invoice, QuoteSnapshot
    db, pid = old_project["db"], old_project["project"].id
    assert touched_projects(db, [pid], JUNE) == {}
    db.add(QuoteSnapshot(quote_id=old_project["quote"].id, version=1, action_type="vision_sync",
                         actor_user_id="vision-delta-tool"))   # the cutover's own snapshot is not a person
    db.flush()
    assert touched_projects(db, [pid], JUNE) == {}
    db.add(Invoice(quote_id=old_project["quote"].id, invoice_sequence=1, quote_version=0, current_version=0))
    db.flush()
    assert "an invoice" in touched_projects(db, [pid], JUNE)[pid]
    db.rollback()


def test_remove_deletes_exactly_the_archived_rows(old_project):
    from scripts.vision_delta.archive import remove_project
    from models import Project, QuoteLineItem
    db = old_project["db"]
    f = _file(old_project)
    assert len(f["velocity"]["quote_line_items"]) == 2 and len(f["velocity"]["projects"]) == 1
    out = remove_project(db, f, JUNE)
    db.commit()
    db.expunge_all()                                       # the rows are gone; drop the stale objects so get() reloads
    assert out["quote_line_items"] == 2 and out["quotes"] == 1 and out["projects"] == 1
    assert db.get(Project, old_project["project_id"]) is None
    assert db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == old_project["quote_id"]).count() == 0


def test_remove_refuses_a_project_that_changed_after_it_was_archived(old_project):
    from scripts.vision_delta.archive import NotSafe, remove_project
    from models import QuoteLineItem
    db = old_project["db"]
    f = _file(old_project)
    li = db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == old_project["quote"].id).first()
    li.description = "changed"
    db.commit()
    with pytest.raises(NotSafe, match="quote_line_items differs"):
        remove_project(db, f, JUNE)
    db.rollback()
    assert db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == old_project["quote"].id).count() == 2


def test_admin_routes_write_read_back_and_remove_once(old_project, tmp_path):
    from fastapi.testclient import TestClient
    from log_store import FolderStore
    import main
    from scripts.vision_delta.archive import PREFIX, sha
    c = TestClient(main.app)
    h = {"X-Cutover-Key": "test-key"}
    f = _file(old_project)
    key = f"{PREFIX}/projects/{f['uca']}.json"
    manifest = {"meta": {"june_day": str(JUNE)}, "files": {key: {"sha256": sha(f), "velocity_project_id": f["velocity_project_id"],
                                                                 "rows": {}}}}
    assert c.put(f"/admin/vision-cutover/archive/files", json={"files": {"../x.json": {}}}, headers=h).status_code == 400
    r = c.put("/admin/vision-cutover/archive/files", json={"files": {key: f, f"{PREFIX}/manifest.json": manifest}}, headers=h)
    assert r.status_code == 200 and r.json()["written"][key] == sha(f)
    body = {"keys": [key], "operator": "test", "june_day": str(JUNE), "estimate": {"wal_bytes": 1000, "db_bytes": 0}}
    assert c.post("/admin/vision-cutover/archive/remove", json=body, headers={"X-Cutover-Key": "no"}).status_code == 403
    r = c.post("/admin/vision-cutover/archive/remove", json=body, headers=h)
    if r.status_code == 409:                               # a busy scratch database: the pre-flight check said wait
        pytest.skip(f"pre-flight check: {r.json()['detail']['wait']}")
    assert r.status_code == 200 and r.json()["removed"][key]["quotes"] == 1, r.text
    again = c.post("/admin/vision-cutover/archive/remove", json=body, headers=h).json()
    assert again["removed"][key]["quotes"] == 1 and not again["refused"]    # answered from the removal record
    got = FolderStore(str(tmp_path / "store")).get_json(key)   # the file stays in the store (read route is table-driven now, #319)
    assert got["uca"] == f["uca"] and c.get("/archive/projects/..%2Fx").status_code in (400, 404)


def test_build_route_builds_a_file_then_finish_writes_the_index(old_project, tmp_path):
    from fastapi.testclient import TestClient
    from log_store import FolderStore
    import main
    from models import Invoice, Project
    from scripts.vision_delta.archive import PREFIX
    c = TestClient(main.app)
    h = {"X-Cutover-Key": "test-key"}
    uca = old_project["project"].uca_project_number
    body = {"active_since": "2018-01-01", "june_day": str(JUNE),
            "projects": [{"uca": uca, "last_activity": "2010-02-01",
                          "vision": {"project": None, "work_orders": [], "pos": []}}]}
    key = f"{PREFIX}/projects/{uca}.json"
    assert c.post("/admin/vision-cutover/archive/build", json=body,
                  headers={"X-Cutover-Key": "no"}).status_code == 403          # wrong key
    r = c.post("/admin/vision-cutover/archive/build", json=body, headers=h)
    assert r.status_code == 200, r.text
    res = r.json()["projects"][uca]
    assert res["key"] == key and res["velocity_project_id"] == old_project["project_id"]
    assert len(res["rows"]["quote_line_items"]) == 2 and len(res["rows"]["projects"]) == 1   # Velocity's rows read here
    assert res["index"]["uca"] == uca and res["index"]["velocity_quotes"] == 1
    got = FolderStore(str(tmp_path / "store")).get_json(key)                    # the file is in the store (read route is table-driven now, #319)
    assert got["uca"] == uca and len(got["velocity"]["quote_line_items"]) == 2
    fin = c.post("/admin/vision-cutover/archive/finish", headers=h, json={
        "meta": {"june_day": str(JUNE)}, "index": [res["index"]],
        "manifest": {key: {"sha256": res["sha256"], "velocity_project_id": res["velocity_project_id"],
                           "rows": res["rows"]}}})
    assert fin.status_code == 200 and fin.json() == {"index": 1, "files": 1}
    assert old_project["db"].get(Project, old_project["project_id"]) is not None   # build only reads the database
    db = old_project["db"]                                                      # a touched project is kept, not built
    db.add(Invoice(quote_id=old_project["quote_id"], invoice_sequence=1, quote_version=0, current_version=0))
    db.commit()
    kept = c.post("/admin/vision-cutover/archive/build", json=body, headers=h).json()["projects"][uca]
    assert kept["kept"] == ["an invoice"]
    db.query(Invoice).filter(Invoice.quote_id == old_project["quote_id"]).delete()
    db.commit()


def test_build_again_after_removal_keeps_the_stored_file(old_project, tmp_path):
    from fastapi.testclient import TestClient
    import main
    from log_store import FolderStore
    from models import QuoteLineItem
    from scripts.vision_delta.archive import PREFIX, sha
    c, h = TestClient(main.app), {"X-Cutover-Key": "test-key"}
    uca = old_project["project"].uca_project_number
    body = {"june_day": str(JUNE), "projects": [{"uca": uca, "last_activity": "2010-02-01",
                                                 "vision": {"project": None, "work_orders": [], "pos": []}}]}
    first = c.post("/admin/vision-cutover/archive/build", json=body, headers=h).json()["projects"][uca]
    store = FolderStore(str(tmp_path / "store"))           # the fixture's store folder
    store.put_json(f"{PREFIX}/removed/{uca}.json", {"uca": uca, "rows": {}})   # as if removal ran
    db = old_project["db"]
    db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == old_project["quote_id"]).delete()
    db.commit()                                            # the live rows changed: a rebuild would differ
    again = c.post("/admin/vision-cutover/archive/build", json=body, headers=h).json()["projects"][uca]
    assert again == first                                  # the stored file, not a rebuild from what is left
    assert sha(store.get_json(first["key"])) == first["sha256"]
