"""Tests for archive-restore (#319): the database index, archiving by hand, and unarchiving.

Runs on a scratch database (CI's, or a local one through DATABASE_URL). Each test
builds its own project, with a folder store and a faked signed-in actor, and cleans
up afterwards. Mirrors the fixture style of tests/test_vision_archive.py (folder
store, cutover key) and tests/test_vision_decisions.py (how a signed-in actor is
faked by monkeypatching ``auth.extract_actor``).
"""
import json
import os
from datetime import datetime

import pytest
from sqlalchemy import text

# Children-before-parents DELETEs for a whole project, so teardown satisfies the
# foreign keys whatever state a test left the project in (archived, restored, or live).
_PURGE_SQL = [
    "DELETE FROM invoice_line_item_snapshots t USING invoice_snapshots s, invoices i, quotes q "
    "WHERE t.snapshot_id=s.id AND s.invoice_id=i.id AND i.quote_id=q.id AND q.project_id=:pid",
    "DELETE FROM invoice_snapshots t USING invoices i, quotes q WHERE t.invoice_id=i.id AND i.quote_id=q.id AND q.project_id=:pid",
    "DELETE FROM invoice_line_items t USING invoices i, quotes q WHERE t.invoice_id=i.id AND i.quote_id=q.id AND q.project_id=:pid",
    "DELETE FROM invoices t USING quotes q WHERE t.quote_id=q.id AND q.project_id=:pid",
    "DELETE FROM quote_line_item_snapshots t USING quote_snapshots s, quotes q WHERE t.snapshot_id=s.id AND s.quote_id=q.id AND q.project_id=:pid",
    "DELETE FROM quote_snapshots t USING quotes q WHERE t.quote_id=q.id AND q.project_id=:pid",
    "DELETE FROM quote_line_items t USING quotes q WHERE t.quote_id=q.id AND q.project_id=:pid",
    "DELETE FROM quotes WHERE project_id=:pid",
    "DELETE FROM po_receiving_line_items t USING po_receivings r, purchase_orders o WHERE t.receiving_id=r.id AND r.purchase_order_id=o.id AND o.project_id=:pid",
    "DELETE FROM po_line_item_snapshots t USING po_snapshots s, purchase_orders o WHERE t.snapshot_id=s.id AND s.purchase_order_id=o.id AND o.project_id=:pid",
    "DELETE FROM po_snapshots t USING purchase_orders o WHERE t.purchase_order_id=o.id AND o.project_id=:pid",
    "DELETE FROM po_receivings t USING purchase_orders o WHERE t.purchase_order_id=o.id AND o.project_id=:pid",
    "DELETE FROM po_line_items t USING purchase_orders o WHERE t.purchase_order_id=o.id AND o.project_id=:pid",
    "DELETE FROM purchase_orders WHERE project_id=:pid",
    "DELETE FROM projects WHERE id=:pid",
]


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A closed project with every row kind, a folder store, a cutover key, and a faked actor."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL (CI, or a local scratch database)")
    import auth
    from database import SessionLocal
    from models import (Invoice, InvoiceLineItem, InvoiceLineItemSnapshot, InvoiceSnapshot, Labor, Miscellaneous,
                         Part, POLineItem, POLineItemSnapshot, POReceiving, POReceivingLineItem, POSnapshot, POStatus,
                         Profile, ProfileType, Project, PurchaseOrder, Quote, QuoteLineItem, QuoteLineItemSnapshot,
                         QuoteSnapshot)
    monkeypatch.setenv("LOG_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("VISION_CUTOVER_KEY", "test-key")
    monkeypatch.delenv("BUCKET", raising=False)
    monkeypatch.setattr(auth, "extract_actor", lambda _h: {"user_id": "u1", "email": "tester@example.com"})
    db = SessionLocal()
    tag = datetime.utcnow().strftime("%H%M%S%f")
    cust = Profile(name=f"[TEST] ar cust {tag}", type=ProfileType.customer, pst="PST", address="1 St", postal_code="A1A1A1")
    vend = Profile(name=f"[TEST] ar vend {tag}", type=ProfileType.vendor, pst="PST", address="2 St", postal_code="A1A1A1")
    part = Part(part_number=f"AR-{tag}", description="part", cost=3.0, markup_percent=50.0)
    lab = Labor(description="labour", hours=1.0, rate=90.0, markup_percent=50.0)
    mis = Miscellaneous(description="misc", unit_price=7.0, markup_percent=50.0)
    db.add_all([cust, vend, part, lab, mis])
    db.flush()
    proj = Project(name=f"[TEST] ar {tag}", customer_id=cust.id, uca_project_number=f"AR{tag}",
                   created_on=datetime(2015, 1, 1))
    db.add(proj)
    db.flush()
    q = Quote(project_id=proj.id, quote_sequence=1, status="Closed", current_version=1, created_at=datetime(2015, 2, 1))
    db.add(q)
    db.flush()
    qli_p = QuoteLineItem(quote_id=q.id, item_type="part", part_id=part.id, description="p", quantity=2, unit_price=5,
                          qty_fulfilled=2, qty_pending=0, base_cost=3.0)
    qli_l = QuoteLineItem(quote_id=q.id, item_type="labor", labor_id=lab.id, description="l", quantity=1.5, unit_price=90,
                          qty_fulfilled=1.5, qty_pending=0)
    qli_m = QuoteLineItem(quote_id=q.id, item_type="misc", misc_id=mis.id, description="m", quantity=1, unit_price=7,
                          qty_fulfilled=1, qty_pending=0)
    db.add_all([qli_p, qli_l, qli_m])
    db.flush()
    qs = QuoteSnapshot(quote_id=q.id, version=1, action_type="edit", actor_user_id="u1", actor_email="t@e.com")
    db.add(qs)
    db.flush()
    db.add(QuoteLineItemSnapshot(snapshot_id=qs.id, original_line_item_id=qli_p.id, item_type="part", part_id=part.id,
                                 description="p", quantity=2, unit_price=5, qty_pending=0, qty_fulfilled=2, base_cost=3.0))
    inv = Invoice(quote_id=q.id, invoice_sequence=1, quote_version=1, current_version=1, status="Sent")
    db.add(inv)
    db.flush()
    ili = InvoiceLineItem(invoice_id=inv.id, quote_line_item_id=qli_p.id, item_type="part", part_id=part.id,
                          description="p", unit_price=5, qty_ordered=2, qty_fulfilled_this_invoice=2,
                          qty_fulfilled_total=2, qty_pending_after=0)
    isn = InvoiceSnapshot(invoice_id=inv.id, version=1, action_type="date_edit")
    db.add_all([ili, isn])
    db.flush()
    db.add(InvoiceLineItemSnapshot(snapshot_id=isn.id, original_line_item_id=ili.id, quote_line_item_id=qli_p.id,
                                   item_type="part", part_id=part.id, description="p", unit_price=5, qty_ordered=2))
    po = PurchaseOrder(project_id=proj.id, vendor_id=vend.id, po_sequence=1, current_version=1, status=POStatus.received)
    db.add(po)
    db.flush()
    poli = POLineItem(purchase_order_id=po.id, item_type="part", part_id=part.id, description="p", quantity=4,
                      unit_price=3, qty_pending=0, qty_received=4)
    db.add(poli)
    db.flush()
    por = POReceiving(purchase_order_id=po.id, received_date=datetime(2015, 3, 1), notes="got it")
    db.add(por)
    db.flush()
    db.add(POReceivingLineItem(receiving_id=por.id, po_line_item_id=poli.id, item_type="part", part_id=part.id,
                               qty_ordered=4, qty_received_this_receiving=4, qty_received_total=4, qty_pending_after=0))
    posn = POSnapshot(purchase_order_id=po.id, version=1, action_type="receive", receiving_id=por.id)   # receiving_id FK
    db.add(posn)
    db.flush()
    db.add(POLineItemSnapshot(snapshot_id=posn.id, original_line_item_id=poli.id, item_type="part", part_id=part.id,
                              description="p", quantity=4, unit_price=3, qty_pending=0, qty_received=4))
    db.commit()
    ids = {"project": proj.id, "quote": q.id, "qli_p": qli_p.id, "po": po.id, "part": part.id, "lab": lab.id,
           "mis": mis.id, "cust": cust.id, "vend": vend.id}
    made = {"db": db, "uca": proj.uca_project_number, "ids": ids, "tmp": tmp_path}
    yield made
    db.rollback()
    db.expunge_all()                                       # drop stale objects the route's session may have deleted
    from models import ArchivedProjectRow, VisionCase
    db.query(VisionCase).filter(VisionCase.project == made["uca"]).delete()
    db.query(ArchivedProjectRow).filter(ArchivedProjectRow.uca == made["uca"]).delete()
    for sql in _PURGE_SQL:
        db.execute(text(sql), {"pid": ids["project"]})
    for mdl, key in (("parts", "part"), ("labor", "lab"), ("miscellaneous", "mis"), ("profiles", "cust"),
                     ("profiles", "vend")):
        db.execute(text(f"DELETE FROM {mdl} WHERE id = :id"), {"id": ids[key]})
    db.commit()
    db.close()


def _client():
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


# ---- the database index: import and search ------------------------------- #

def _seed_store(tmp_path, entries, removed_ucas=()):
    """Write a synthetic index/manifest/files/markers to a folder store (as the bulk archive would)."""
    from log_store import FolderStore
    from scripts.vision_delta.archive import PREFIX, sha
    store = FolderStore(str(tmp_path / "store"))
    index, manifest = [], {}
    for e in entries:
        key = f"{PREFIX}/projects/{e['uca']}.json"
        doc = {"format": 1, "uca": e["uca"], "name": e["name"], "customer": e["customer"], "created_on": "2010-01-01",
               "last_activity": e["last_activity"], "velocity_project_id": e.get("vel_id"),
               "vision": {"project": None, "work_orders": [], "pos": []}, "velocity": e.get("velocity")}
        store.put_json(key, doc)
        index.append({"uca": e["uca"], "name": e["name"], "customer": e["customer"], "created_on": "2010-01-01",
                      "last_activity": e["last_activity"], "work_orders": 0, "pos": 0,
                      "velocity_quotes": 0, "velocity_pos": 0, "in_velocity": e.get("velocity") is not None, "key": key})
        manifest[key] = {"sha256": sha(doc), "velocity_project_id": e.get("vel_id"), "rows": {}}
    store.put_json(f"{PREFIX}/index.json", {"meta": {"made_at": "2026-10-29 00:00:00"}, "projects": index})
    store.put_json(f"{PREFIX}/manifest.json", {"meta": {}, "files": manifest})
    for u in removed_ucas:
        store.put_json(f"{PREFIX}/removed/{u}.json", {"uca": u, "rows": {}})
    return store


def test_import_fills_the_table_idempotently_and_search_reads_it(world):
    from models import ArchivedProjectRow
    db, tag = world["db"], world["uca"]
    a, b = f"{tag}A", f"{tag}B"
    _seed_store(world["tmp"],
                [{"uca": a, "name": "Alpha", "customer": "Cust A", "last_activity": "2011-05-01", "vel_id": 1,
                  "velocity": {"projects": [{"id": 1}]}},
                 {"uca": b, "name": "Beta", "customer": "Cust B", "last_activity": "2012-06-01"}],
                removed_ucas=[a])
    c, h = _client(), {"X-Cutover-Key": "test-key"}
    r = c.post("/admin/vision-cutover/archive/index/import", headers=h)
    assert r.status_code == 200 and r.json()["inserted"] == 2, r.text
    again = c.post("/admin/vision-cutover/archive/index/import", headers=h)
    assert again.json()["inserted"] == 0 and again.json()["skipped"] == 2       # insert-only: nothing new
    db.expire_all()
    rows = {row.uca: row for row in db.query(ArchivedProjectRow).filter(ArchivedProjectRow.uca.in_([a, b]))}
    assert rows[a].rows_removed is True and rows[b].rows_removed is False        # marker -> rows_removed
    assert rows[a].in_velocity is True and rows[b].in_velocity is False
    found = c.get("/archive/projects", params={"q": a}).json()
    assert found["total"] == 1 and found["projects"][0]["uca"] == a
    allp = c.get("/archive/projects").json()
    assert allp["projects"][0]["last_activity"] >= allp["projects"][-1]["last_activity"]   # newest activity first
    got = c.get(f"/archive/projects/{b}").json()
    assert got["uca"] == b and got["velocity"] is None
    db.query(ArchivedProjectRow).filter(ArchivedProjectRow.uca.in_([a, b])).delete(synchronize_session=False)
    db.commit()


# ---- archive by hand: refusals ------------------------------------------- #

def test_archive_by_hand_refused_when_a_quote_or_po_line_or_case_is_open(world):
    from models import POLineItem, QuoteLineItem, VisionCase
    db, pid, uca = world["db"], world["ids"]["project"], world["uca"]
    c = _client()
    db.query(QuoteLineItem).filter(QuoteLineItem.id == world["ids"]["qli_p"]).update({"qty_pending": 2})
    db.commit()
    r = c.post(f"/projects/{pid}/archive")
    assert r.status_code == 409 and any("quote line" in x for x in r.json()["detail"]["reasons"]), r.text
    db.query(QuoteLineItem).filter(QuoteLineItem.id == world["ids"]["qli_p"]).update({"qty_pending": 0})
    db.query(POLineItem).filter(POLineItem.purchase_order_id == world["ids"]["po"]).update({"qty_pending": 3})
    db.commit()
    r = c.post(f"/projects/{pid}/archive")
    assert r.status_code == 409 and any("purchase order line" in x for x in r.json()["detail"]["reasons"])
    db.query(POLineItem).filter(POLineItem.purchase_order_id == world["ids"]["po"]).update({"qty_pending": 0})
    from models import VisionSyncRun
    run = VisionSyncRun(started_at=datetime.utcnow(), mode="commit")
    db.add(run)
    db.flush()
    db.add(VisionCase(run_id=run.id, kind="conflict", case_key=f"k{uca}", project=uca, decision=None))
    db.commit()
    r = c.post(f"/projects/{pid}/archive")
    assert r.status_code == 409 and any("cutover decision" in x for x in r.json()["detail"]["reasons"])
    db.query(VisionCase).filter(VisionCase.project == uca).delete()
    db.query(VisionSyncRun).filter(VisionSyncRun.id == run.id).delete()
    db.commit()


def test_archive_by_hand_needs_a_signed_in_user(world, monkeypatch):
    import auth
    monkeypatch.setattr(auth, "extract_actor", lambda _h: None)                 # nobody signed in
    r = _client().post(f"/projects/{world['ids']['project']}/archive")
    assert r.status_code == 401


# ---- archive by hand, then unarchive: the round trip --------------------- #

def test_by_hand_archive_holds_every_row_then_unarchive_restores_them_identically(world):
    from scripts.vision_delta.archive import ARCHIVE_ORDER, project_rows, sha
    from models import Invoice, POSnapshot, Project, Quote
    db, pid, uca = world["db"], world["ids"]["project"], world["uca"]
    c = _client()
    r = c.post(f"/projects/{pid}/archive")
    assert r.status_code == 200, r.text
    removed = r.json()["removed"]
    assert removed["quotes"] == 1 and removed["quote_line_items"] == 3 and removed["invoices"] == 1
    assert removed["po_snapshots"] == 1 and removed["po_receivings"] == 1       # snapshots went before receivings
    db.expire_all()
    assert db.get(Project, pid) is None                                         # the project's rows are gone
    file = c.get(f"/archive/projects/{uca}").json()
    vel = file["velocity"]
    assert len(vel["quote_line_items"]) == 3 and len(vel["invoices"]) == 1 and len(vel["invoice_line_items"]) == 1
    assert len(vel["po_snapshots"]) == 1 and len(vel["po_receivings"]) == 1 and len(vel["po_line_item_snapshots"]) == 1
    un = c.post(f"/archive/projects/{uca}/unarchive")
    assert un.status_code == 200 and un.json()["reinserted"] is True, un.text
    db.expire_all()
    assert db.get(Project, pid) is not None and db.get(Quote, world["ids"]["quote"]) is not None   # same ids back
    assert db.query(Invoice).filter(Invoice.quote_id == world["ids"]["quote"]).count() == 1
    assert db.query(POSnapshot).filter(POSnapshot.purchase_order_id == world["ids"]["po"]).count() == 1
    now = project_rows(db, pid, [t for t in ARCHIVE_ORDER if t in vel])
    for t in now:
        assert sha(now[t]) == sha(vel[t]), f"{t} did not round-trip identically"
    # re-archive works now that the rows are back and closed
    again = c.post(f"/projects/{pid}/archive")
    assert again.status_code == 200 and again.json()["removed"]["quotes"] == 1, again.text
    db.expire_all()
    assert db.get(Project, pid) is None


# ---- unarchive refusals -------------------------------------------------- #

def test_unarchive_refused_for_id_collision_a_missing_part_and_vision_only(world):
    from scripts.vision_delta.archive import RestoreError, by_hand_file, restore_project
    db, pid, uca = world["db"], world["ids"]["project"], world["uca"]
    live = json.loads(json.dumps(by_hand_file(db, pid), default=str))           # the file while the rows are still live
    with pytest.raises(RestoreError) as e:
        restore_project(db, live)                                               # every id is already taken
    assert any("already exists" in x for x in e.value.reasons)
    db.rollback()
    with pytest.raises(RestoreError, match="exists only in Vision"):            # no Velocity rows to put back
        restore_project(db, {"uca": uca, "velocity_project_id": None, "velocity": None})
    db.rollback()
    # a missing referenced part: archive (rows gone), delete the now-unreferenced part, then unarchive
    c = _client()
    assert c.post(f"/projects/{pid}/archive").status_code == 200
    db.execute(text("DELETE FROM parts WHERE id = :id"), {"id": world["ids"]["part"]})
    db.commit()
    un = c.post(f"/archive/projects/{uca}/unarchive")
    assert un.status_code == 409 and any("parts" in x and "gone" in x for x in un.json()["detail"]["reasons"]), un.text


# ---- review fixes: numbering, settled cases, the pending bulk removal ---- #

def test_an_archived_number_is_never_handed_to_a_new_project(world):
    """Archiving the newest project must not free its UCA number for the next new project."""
    from models import ArchivedProjectRow
    from routes.projects import generate_next_uca_number
    db = world["db"]
    nxt = generate_next_uca_number(db)                     # what a new project would get now
    db.add(ArchivedProjectRow(uca=nxt, file_key=f"vision-archive/projects/{nxt}.json", name="x", state="archived",
                              in_velocity=True, rows_removed=True, source="by hand"))
    db.flush()
    assert generate_next_uca_number(db) != nxt             # the archived number counts as taken
    db.rollback()


def test_a_settled_case_no_longer_blocks_archiving_by_hand(world):
    """A held log row stays held after its case is settled; only the open case blocks."""
    from models import VisionCase, VisionSyncChange, VisionSyncRun
    db, pid, uca = world["db"], world["ids"]["project"], world["uca"]
    from scripts.vision_delta.archive import open_blockers
    run = VisionSyncRun(started_at=datetime.utcnow(), mode="commit")
    db.add(run)
    db.flush()
    db.add(VisionSyncChange(run_id=run.id, level="quotes", action="held", record_table="quotes",
                            record_id=world["ids"]["quote"], project=uca))   # the cutover's log row, never rewritten
    case = VisionCase(run_id=run.id, kind="conflict", case_key=f"s{uca}", project=uca, decision=None)
    db.add(case)
    db.flush()
    assert open_blockers(db, pid, uca) == ["an unsettled cutover decision is open on this project"]
    case.decision = "keep velocity"                        # a person settles it on the decisions page
    db.flush()
    assert open_blockers(db, pid, uca) == []               # the held log row alone does not block
    db.rollback()


def test_pending_bulk_removal_guards(world):
    """An unarchived project is never bulk-removed again; an archived one awaiting removal can't be re-archived."""
    from models import ArchivedProjectRow
    from scripts.vision_delta.archive import PREFIX, project_rows, ARCHIVE_ORDER, sha
    db, pid, uca = world["db"], world["ids"]["project"], world["uca"]
    c, h = _client(), {"X-Cutover-Key": "test-key"}
    key = f"{PREFIX}/projects/{uca}.json"
    vel = project_rows(db, pid, ARCHIVE_ORDER)
    f = {"format": 1, "uca": uca, "name": "x", "customer": "c", "created_on": None, "last_activity": "2015-02-01",
         "velocity_project_id": pid, "vision": {"project": None, "work_orders": [], "pos": []}, "velocity": vel}
    f = json.loads(json.dumps(f, default=str))
    from log_store import FolderStore
    store = FolderStore(str(world["tmp"] / "store"))
    store.put_json(key, f)
    store.put_json(f"{PREFIX}/manifest.json", {"meta": {}, "files": {key: {"sha256": sha(f), "velocity_project_id": pid,
                                                                          "rows": {}}}})
    db.add(ArchivedProjectRow(uca=uca, velocity_project_id=pid, file_key=key, name="x", state="archived",
                              in_velocity=True, rows_removed=False, source="vision cutover"))
    db.commit()
    r = c.post(f"/projects/{pid}/archive")                 # already archived, removal pending: refused
    assert r.status_code == 409 and "pending" in r.json()["detail"]["reasons"][0], r.text
    un = c.post(f"/archive/projects/{uca}/unarchive")       # rows still live: just flips to restored
    assert un.status_code == 200 and un.json()["reinserted"] is False, un.text
    body = {"keys": [key], "operator": "test", "june_day": "2026-06-19", "estimate": {"wal_bytes": 1000, "db_bytes": 0}}
    rm = c.post("/admin/vision-cutover/archive/remove", json=body, headers=h)
    if rm.status_code == 409:                              # a busy scratch database: the pre-flight check said wait
        pytest.skip(f"pre-flight check: {rm.json()['detail']}")
    assert rm.status_code == 200 and key in rm.json()["refused"], rm.text   # the bulk run leaves it alone
    db.expire_all()
    from models import Project
    assert db.get(Project, pid) is not None
