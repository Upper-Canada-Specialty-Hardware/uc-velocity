"""Tests for the decisions page (#297): building the cases and settling them.

The first half needs no database: how log rows group into cases, the
predicted answers, and the stale check. The second half runs the endpoints on
a scratch database (CI's, or a local one through DATABASE_URL): each test makes
its own project, quote, run and case, and removes them afterwards.
"""
import os
from datetime import datetime

import pytest

from scripts.vision_delta import cases as cs


def _log(i, level="quote lines", action="kept: conflict", table="quote_line_items", rid=10, field="quantity",
         before="2.0", after="3.0", reason="unexplained | unexplained: velocity kept, needs a decision",
         context="A1-0001-0 (WO 5) part P1", project="A1", vision_key="77"):
    return {"id": i, "level": level, "action": action, "record_table": table, "record_id": rid,
            "vision_key": vision_key, "project": project, "context": context, "field": field,
            "before": before, "after": after, "reason": reason}


# ---- building cases (no database) ------------------------------------------

def test_conflict_rows_group_by_velocity_record():
    rows = [_log(1, field="quantity"), _log(2, field="unit_price"), _log(3, rid=11)]
    out = {c["case_key"]: c for c in cs.change_cases(rows)}
    assert set(out) == {"quote_line_items:10", "quote_line_items:11"}
    assert out["quote_line_items:10"]["change_ids"] == [1, 2] and out["quote_line_items:10"]["kind"] == "conflict"


def test_credit_rows_group_by_work_order_and_test_projects_by_project():
    held = "june import rule | held for a decision: line of a credit-only work order"
    rows = [_log(1, action="held", reason=held, context="A1-0001-0 (WO 6648) part M09-001", after="-3.0"),
            _log(2, action="held", reason=held, rid=12, context="A1-0001-0 (WO 6648) labor X", after="-1.0"),
            _log(3, level="quotes", action="held", table="quotes", rid=None, field=None,
                 reason="held for a decision: credit-only work order", context="WO 13652 A3027 Work Order"),
            _log(4, level="projects", action="held", table="projects", rid=None, field=None, project="vision A3823",
                 reason="held for a decision: project name reads as test data", context="A3823 DO NOT USE"),
            _log(5, level="quotes", action="held", table="quotes", rid=None, field=None, project="vision A3823",
                 reason="held for a decision: its project is held (project name reads as test data)")]
    out = {c["case_key"]: c for c in cs.change_cases(rows)}
    assert out["wo:6648"]["change_ids"] == [1, 2] and out["wo:6648"]["title"] == "A1-0001-0 (WO 6648)"
    assert out["wo:13652"]["kind"] == "credit memo" and out["wo:13652"]["prediction"] is None   # not in Velocity
    assert out["project:vision A3823"]["change_ids"] == [4, 5] and out["project:vision A3823"]["project"] == "A3823"


@pytest.mark.parametrize("rows, expected", [
    ([_log(1)], "velocity"),                                                        # only Velocity moved
    ([_log(1, before=None, after="PO120655")], "vision"),                           # Velocity blank
    ([_log(1, reason="changed in vision after june | different inventory item: velocity kept")], None),
    ([_log(1, field=None, rid=None, before=None, after=None)], "left out"),         # a line Velocity dropped
])
def test_conflict_prediction(rows, expected):
    assert cs.predict_conflict(rows)[0] == expected


def test_credit_prediction_waits_for_negative_quantities_when_a_credit_is_pending():
    done = [_log(1, field="quantity", after="-2.0"), _log(2, field="qty_fulfilled", after="-2.0")]
    pending = [_log(1, field="quantity", after="-2.0"), _log(2, field="qty_pending", after="-2.0")]
    assert cs.predict_credit(done)[0] == "vision"
    assert cs.predict_credit(pending)[0] is None and "#277" in cs.predict_credit(pending)[1]


def test_report_prediction_text_maps_to_options():
    assert cs._predict_from_text("likely a separate job", "same job") == "separate jobs"
    assert cs._predict_from_text("likely the same job typed into both systems", "same job") == "same job"
    assert cs._predict_from_text("likely a genuine duplicate: nothing else differs", "duplicate project") == "duplicate"
    assert cs._predict_from_text("not a duplicate", "duplicate project") is None
    assert cs._predict_from_text(None, "same order") is None


def test_every_prediction_is_one_of_its_kinds_options():
    for kind, _, options, _ in cs.KINDS:
        assert options and len(set(options)) == len(options), kind
    for c in cs.change_cases([_log(1), _log(2, before=None, rid=11)]):
        assert c["prediction"] in cs.OPTIONS[c["kind"]]


# ---- the stale check (no database) ------------------------------------------

def test_same_treats_numbers_blanks_and_line_endings_loosely():
    from routes.vision_decisions import same
    assert same(50, "50.0") and same(None, "") and same("  ", None) and same("a\r\nb", "a\nb")
    assert same(datetime(2026, 6, 18, 20, 14, 24, 602757), "2026-06-18 20:14:24.602757")
    assert not same(2.0, "3.0") and not same(None, "x") and not same("A", "B")


def test_a_logged_archived_project_status_reads_closed():
    """Cases logged before migration 037 say "archived"; the page reads and writes "closed" (#330)."""
    from routes.vision_decisions import _relabel
    row = _log(1, level="projects", table="projects", rid=5, field="status", before="active", after="archived")
    row.update(velocity=row.pop("before"), vision=row.pop("after"))         # the case-detail shape
    out = _relabel(row)
    assert out["vision"] == "closed" and out["velocity"] == "active" and row["vision"] == "archived"   # a copy
    other = dict(row, field="name", vision="archived")
    assert _relabel(other) is other                                          # only the project status is renamed


# ---- the endpoints (scratch database) ---------------------------------------

@pytest.fixture
def world(monkeypatch):
    """A project, a quote with one part line, a cutover run, and a cleanup afterwards."""
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs DATABASE_URL (CI, or a local scratch database)")
    import auth
    from database import SessionLocal
    from models import (Profile, ProfileType, Project, Quote, QuoteLineItem, QuoteLineItemSnapshot, QuoteSnapshot,
                        VisionCase, VisionSyncRun)
    monkeypatch.setattr(auth, "extract_actor", lambda _h: {"user_id": "u1", "email": "tester@example.com"})
    db = SessionLocal()
    tag = datetime.utcnow().strftime("%H%M%S%f")
    cust = Profile(name=f"[TEST] decisions {tag}", type=ProfileType.customer, pst="PST-TEST", address="1 Test St",
                   postal_code="A1A1A1")
    db.add(cust)
    db.flush()
    proj = Project(name=f"[TEST] decisions {tag}", customer_id=cust.id, uca_project_number=f"TD{tag}")
    db.add(proj)
    db.flush()
    q = Quote(project_id=proj.id, quote_sequence=1, status="Work Order", current_version=0)
    db.add(q)
    db.flush()
    li = QuoteLineItem(quote_id=q.id, item_type="misc", description="Service", quantity=2, unit_price=10,
                       qty_fulfilled=0, qty_pending=2)
    run = VisionSyncRun(started_at=datetime.utcnow(), mode="commit")
    db.add_all([li, run])
    db.commit()
    made = {"db": db, "project": proj, "quote": q, "line": li, "run": run, "customer": cust, "tag": tag,
            "projects": [proj.id], "profiles": [cust.id]}   # tests add what they make, for the cleanup
    yield made
    db.rollback()
    db.query(VisionCase).filter(VisionCase.run_id == run.id).delete()
    for pid in made["projects"]:                           # everything left in each test project
        _wipe_project(db, pid)
    db.query(Profile).filter(Profile.id.in_(made["profiles"])).delete(synchronize_session=False)
    db.query(VisionSyncRun).filter(VisionSyncRun.id == run.id).delete()
    db.commit()
    db.close()


def _wipe_project(db, pid):
    """Remove one test project with its quotes, POs, lines and audit entries."""
    from models import (POLineItem, POLineItemSnapshot, POSnapshot, Project, PurchaseOrder, Quote, QuoteLineItem,
                        QuoteLineItemSnapshot, QuoteSnapshot)
    qids = [i for (i,) in db.query(Quote.id).filter(Quote.project_id == pid)]
    sids = [i for (i,) in db.query(QuoteSnapshot.id).filter(QuoteSnapshot.quote_id.in_(qids))]
    db.query(QuoteLineItemSnapshot).filter(QuoteLineItemSnapshot.snapshot_id.in_(sids)).delete(synchronize_session=False)
    db.query(QuoteSnapshot).filter(QuoteSnapshot.id.in_(sids)).delete(synchronize_session=False)
    db.query(QuoteLineItem).filter(QuoteLineItem.quote_id.in_(qids)).delete(synchronize_session=False)
    db.query(Quote).filter(Quote.id.in_(qids)).delete(synchronize_session=False)
    pids = [i for (i,) in db.query(PurchaseOrder.id).filter(PurchaseOrder.project_id == pid)]
    psids = [i for (i,) in db.query(POSnapshot.id).filter(POSnapshot.purchase_order_id.in_(pids))]
    db.query(POLineItemSnapshot).filter(POLineItemSnapshot.snapshot_id.in_(psids)).delete(synchronize_session=False)
    db.query(POSnapshot).filter(POSnapshot.id.in_(psids)).delete(synchronize_session=False)
    db.query(POLineItem).filter(POLineItem.purchase_order_id.in_(pids)).delete(synchronize_session=False)
    db.query(PurchaseOrder).filter(PurchaseOrder.id.in_(pids)).delete(synchronize_session=False)
    db.query(Project).filter(Project.id == pid).delete(synchronize_session=False)


_KEYS = __import__("itertools").count()                  # unique case keys within a test's run


def _case(w, kind="conflict", rows=(), prediction="vision", found=None):
    from models import VisionCase
    c = VisionCase(run_id=w["run"].id, kind=kind, case_key=f"k{next(_KEYS)}", title="t",
                   prediction=prediction, detail={"rows": list(rows), "found": found}, change_ids=[])
    w["db"].add(c)
    w["db"].commit()
    return c


def _line_row(w, field, velocity, vision, i=1):
    return {"change_id": i, "level": "quote lines", "field": field, "record_table": "quote_line_items",
            "record_id": w["line"].id, "velocity": velocity, "vision": vision, "cause": "unexplained",
            "context": "line"}


def _client():
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def test_use_vision_writes_the_line_with_one_snapshot_and_records_who(world):
    from models import QuoteSnapshot, VisionCase
    c = _case(world, rows=[_line_row(world, "quantity", "2.0", "3.0")])
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "vision"})
    assert r.status_code == 200, r.text
    db = world["db"]
    db.expire_all()
    li = world["line"]
    assert (li.quantity, li.qty_fulfilled, li.qty_pending) == (3, 0, 3)          # pending follows the quantity
    snaps = db.query(QuoteSnapshot).filter(QuoteSnapshot.quote_id == world["quote"].id).all()
    assert [s.action_type for s in snaps] == ["vision_sync"]
    assert world["quote"].item_list_version in (None, 0)                          # the visible number stays
    case = db.get(VisionCase, c.id)
    assert case.decision == "vision" and case.decided_by == "tester@example.com" and case.decided_at


def test_use_vision_refuses_when_velocity_changed_since_and_writes_nothing(world):
    c = _case(world, rows=[_line_row(world, "quantity", "5.0", "3.0")])          # Velocity holds 2, not 5
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "vision"})
    assert r.status_code == 409 and "changed in Velocity" in r.json()["detail"]
    world["db"].expire_all()
    assert world["line"].quantity == 2 and world["db"].get(type(c), c.id).decision is None


def test_keep_velocity_adds_the_vision_version_to_the_audit_trail(world):
    from models import QuoteSnapshot
    c = _case(world, rows=[_line_row(world, "quantity", "2.0", "3.0")], prediction="velocity")
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "velocity", "note": "checked"})
    assert r.status_code == 200, r.text
    world["db"].expire_all()
    assert world["line"].quantity == 2                                            # Velocity's value stays
    snap = world["db"].query(QuoteSnapshot).filter(QuoteSnapshot.quote_id == world["quote"].id).one()
    assert snap.action_type == "vision_version" and "Vision 3.0, Velocity 2.0" in snap.action_description
    assert r.json()["decision_note"] == "checked"


def test_a_settled_case_cannot_be_settled_again_and_answers_are_checked(world):
    c = _case(world, kind="test project", prediction="left out")
    cl = _client()
    assert cl.post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "vision"}).status_code == 409
    assert cl.post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "left out"}).status_code == 200
    again = cl.post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "entered by hand"})
    assert again.status_code == 409 and "already settled" in again.json()["detail"]


# ---- merges (#341): one record kept, the other in its audit trail, then deleted ----

_WO = __import__("itertools").count(900_000_000 + int(datetime.utcnow().strftime("%H%M%S")) * 1000)   # free Vision keys


def _vision_quote(w, project=None, seq=2, edited=False):
    """A quote as the cutover adds it from Vision: import key, WO #, one line, a vision_sync entry."""
    from models import Quote, QuoteLineItem
    from routes.quotes import create_snapshot
    db, wo = w["db"], next(_WO)
    q = Quote(project_id=(project or w["project"]).id, quote_sequence=seq, status="Draft", current_version=0,
              legacy_imported=True, vision_wo_number=wo, legacy_source="tblServiceRecords", legacy_id=wo)
    db.add(q)
    db.flush()
    db.add(QuoteLineItem(quote_id=q.id, item_type="misc", description="Vision line", quantity=4, unit_price=5,
                         qty_fulfilled=0, qty_pending=4))
    db.flush()
    create_snapshot(db, q, "vision_sync", "Added from Vision")
    if edited:
        create_snapshot(db, q, "edit", "a person changed it")   # touched in Velocity after the cutover
    db.commit()
    return q, wo


def test_same_job_merges_the_added_quote_into_the_velocity_quote(world):
    from models import Quote, QuoteSnapshot
    gone, wo = _vision_quote(world)
    gone_id = gone.id
    c = _case(world, kind="same job", prediction="same job",
              found={"vision WO": str(wo), "velocity quote id": str(world["quote"].id)})
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "same job"})
    assert r.status_code == 200, r.text
    db = world["db"]
    db.expire_all()
    keep = world["quote"]
    assert db.get(Quote, gone_id) is None                                   # the duplicate is gone
    assert (keep.vision_wo_number, keep.legacy_source, keep.legacy_id) == (wo, "tblServiceRecords", wo)
    snaps = db.query(QuoteSnapshot).filter(QuoteSnapshot.quote_id == keep.id).order_by(QuoteSnapshot.version).all()
    assert [s.action_type for s in snaps] == ["vision_version", "vision_version"]
    assert [l.description for l in snaps[0].line_item_states] == ["Vision line"]   # the deleted quote's lines
    assert [l.description for l in snaps[1].line_item_states] == ["Service"]       # newest matches the quote
    assert keep.item_list_version in (None, 0)                              # its visible number stays
    assert r.json()["result"]["deleted"].endswith("-0002-0")


def test_a_merge_refuses_to_delete_a_quote_a_person_edited(world):
    from models import Quote
    gone, wo = _vision_quote(world, edited=True)
    c = _case(world, kind="same job", prediction="same job",
              found={"vision WO": str(wo), "velocity quote id": str(world["quote"].id)})
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "same job"})
    assert r.status_code == 409 and "edited in Velocity" in r.json()["detail"]
    world["db"].expire_all()
    assert world["db"].get(Quote, gone.id) is not None and world["quote"].vision_wo_number is None


def test_copy_replaces_original_keeps_the_copy_and_deletes_the_original(world):
    from models import Quote
    tag = world["project"].uca_project_number
    original, wo = _vision_quote(world, seq=2)                              # the June quote
    original_id = original.id                                               # read before it is deleted
    c = _case(world, kind="velocity copy", prediction=None,
              found={"original": f"{tag}-0002-0", "copy": f"{tag}-0001-0", "vision work order": str(wo)})
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "copy replaces original"})
    assert r.status_code == 200, r.text
    world["db"].expire_all()
    assert world["db"].get(Quote, original_id) is None and world["quote"].legacy_id == wo


def test_same_order_merges_the_added_po_into_the_velocity_po(world):
    from models import POLineItem, POSnapshot, Profile, ProfileType, PurchaseOrder
    from routes.purchase_orders import create_po_snapshot
    db = world["db"]
    vendor = Profile(name=f"[TEST] vendor {world['tag']}", type=ProfileType.vendor, pst="PST-TEST",
                     address="1 Test St", postal_code="A1A1A1")
    db.add(vendor)
    db.flush()
    world["profiles"].append(vendor.id)
    keep = PurchaseOrder(project_id=world["project"].id, vendor_id=vendor.id, po_sequence=1, current_version=0)
    gone = PurchaseOrder(project_id=world["project"].id, vendor_id=vendor.id, po_sequence=2, current_version=0,
                         legacy_imported=True, vision_po_number=f"V-{world['tag']}", legacy_source="tblPurchaseOrders",
                         legacy_id=next(_WO))
    db.add_all([keep, gone])
    db.flush()
    db.add(POLineItem(purchase_order_id=gone.id, item_type="misc", description="Vision PO line", quantity=3,
                      unit_price=2, qty_pending=3, qty_received=0))
    db.flush()
    create_po_snapshot(db, gone, "create", "Added from Vision")
    db.commit()
    gone_id = gone.id
    c = _case(world, kind="same order", prediction="same order",
              found={"vision PO": gone.vision_po_number, "velocity PO": f"PO-{world['project'].uca_project_number}-0001-0"})
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "same order"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(PurchaseOrder, gone_id) is None and keep.vision_po_number == f"V-{world['tag']}"
    snaps = db.query(POSnapshot).filter(POSnapshot.purchase_order_id == keep.id).order_by(POSnapshot.id).all()
    assert [l.description for l in snaps[0].line_item_states] == ["Vision PO line"]


def test_duplicate_moves_the_records_and_deletes_the_emptied_project(world):
    from models import Project, Quote, QuoteSnapshot
    db = world["db"]
    other = Project(name="[TEST] twin", customer_id=world["customer"].id, uca_project_number=f"TE{world['tag']}",
                    legacy_source="tblProjects", legacy_id=next(_WO))
    db.add(other)
    db.flush()
    world["projects"].append(other.id)
    other_id, other_uca = other.id, other.uca_project_number                # read before it is deleted
    moved, _ = _vision_quote(world, project=other, seq=1)                   # what the cutover put in the twin
    found = [{"uca": world["project"].uca_project_number, "quotes": "1", "pos": "0", "created": "2026-07-17 13:15:19"},
             {"uca": other_uca, "quotes": "0", "pos": "0", "created": "2026-08-12 14:43:55"}]
    c = _case(world, kind="duplicate project", prediction="duplicate", found=found)
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "duplicate"})
    assert r.status_code == 200, r.text
    db.expire_all()
    keep = world["project"]
    assert db.get(Project, other_id) is None                                # the emptied twin is gone
    q = db.get(Quote, moved.id)
    assert q.project_id == keep.id and q.quote_sequence == 2                # renumbered into the kept project
    assert keep.vision_project_number == other_uca and keep.legacy_source == "tblProjects"
    last = db.query(QuoteSnapshot).filter(QuoteSnapshot.quote_id == q.id).order_by(QuoteSnapshot.version.desc()).first()
    assert last.action_type == "move" and f"({other_uca}-0001-0)" in last.action_description
    assert r.json()["result"]["new_cases"] == []                            # nothing in common: no pair to decide


def test_duplicate_opens_a_same_job_case_for_a_look_alike_and_its_answer_merges(world):
    from models import Project, Quote, QuoteLineItem, VisionCase
    db = world["db"]
    other = Project(name="[TEST] twin", customer_id=world["customer"].id, uca_project_number=f"TF{world['tag']}")
    db.add(other)
    db.flush()
    world["projects"].append(other.id)
    other_uca = other.uca_project_number
    twin, wo = _vision_quote(world, project=other, seq=1)
    db.add(QuoteLineItem(quote_id=twin.id, item_type="misc", description="Service", quantity=2, unit_price=10,
                         qty_fulfilled=0, qty_pending=2))                   # the same item as the Velocity quote
    db.commit()
    twin_id = twin.id
    found = [{"uca": world["project"].uca_project_number, "quotes": "1", "pos": "0", "created": "2026-07-17 13:15:19"},
             {"uca": other_uca, "quotes": "0", "pos": "0", "created": "2026-08-12 14:43:55"}]
    c = _case(world, kind="duplicate project", prediction="duplicate", found=found)
    cl = _client()
    r = cl.post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "duplicate"})
    assert r.status_code == 200, r.text
    assert len(r.json()["result"]["new_cases"]) == 1                        # the pair is put to a person
    pair = db.query(VisionCase).filter(VisionCase.run_id == world["run"].id, VisionCase.kind == "same job").one()
    assert pair.prediction is None and pair.detail["found"]["vision WO"] == str(wo)
    r = cl.post(f"/vision-decisions/cases/{pair.id}/decide", json={"decision": "same job"})
    assert r.status_code == 200, r.text                                     # and the answer is carried out
    db.expire_all()
    assert db.get(Quote, twin_id) is None and world["quote"].vision_wo_number == wo


def test_carry_out_merges_cases_settled_before_merges_existed(world):
    from models import Quote, VisionCase
    gone, wo = _vision_quote(world)
    gone_id = gone.id
    c = _case(world, kind="same job", prediction="same job",
              found={"vision WO": str(wo), "velocity quote id": str(world["quote"].id)})
    c.decision, c.decided_by, c.result = "same job", "earlier@example.com", {"quote": world["quote"].id}
    world["db"].commit()
    cl = _client()
    assert {s["kind"]: s for s in cl.get("/vision-decisions/steps").json()}["same job"]["unmerged"] >= 1
    r = cl.post("/vision-decisions/steps/same job/carry-out")
    assert r.status_code == 200 and c.id in r.json()["merged"], r.text
    world["db"].expire_all()
    case = world["db"].get(VisionCase, c.id)
    assert world["db"].get(Quote, gone_id) is None and case.decided_by == "earlier@example.com"
    assert case.result["merged"] == "quotes" and case.result["carried_out_by"] == "tester@example.com"


def test_accept_settles_predicted_cases_and_reports_refusals(world):
    good = _case(world, kind="conflict", rows=[_line_row(world, "description", "Service", "Service call")])
    bad = _case(world, kind="conflict", rows=[_line_row(world, "quantity", "9.0", "3.0", i=2)])
    r = _client().post("/vision-decisions/steps/conflict/accept", json={"case_ids": [good.id, bad.id]})
    assert r.status_code == 200, r.text
    assert r.json()["settled"] == [good.id] and [x["id"] for x in r.json()["refused"]] == [bad.id]
    world["db"].expire_all()
    assert world["line"].description == "Service call"


def test_steps_and_cases_list_open_work(world):
    _case(world, kind="velocity copy", prediction=None)
    cl = _client()
    steps = {s["kind"]: s for s in cl.get("/vision-decisions/steps").json()}
    assert [s for s in steps] == [k for k, _, _, _ in cs.KINDS] and steps["velocity copy"]["open"] >= 1
    page = cl.get("/vision-decisions/cases", params={"kind": "velocity copy"}).json()
    assert page["total"] >= 1 and page["cases"][0]["options"] == cs.OPTIONS["velocity copy"]


# ---- the admin load route (#297): build cases on the server, insert what is new ----

def _finding(kind, key, prediction):
    """A findings case dict as cases.finding_cases emits, for the load route's body."""
    return {"kind": kind, "case_key": key, "project": "A1", "title": f"{kind} {key}",
            "prediction": prediction, "prediction_reason": "likely", "detail": {"found": {}}, "change_ids": []}


def test_cases_load_route_dry_run_then_commit_and_is_idempotent(world, monkeypatch):
    from models import VisionCase
    monkeypatch.setenv("VISION_CUTOVER_KEY", "test-key")         # the admin API is on only with a key set
    run_id = world["run"].id
    findings = [_finding("same job", f"wo:{run_id}:1", "same job"),
                _finding("duplicate project", f"group:{run_id}", "duplicate")]
    body = {"run_id": run_id, "findings": findings, "commit": False}
    c, h = _client(), {"X-Cutover-Key": "test-key"}
    assert c.post("/admin/vision-cutover/cases/load", json=body,
                  headers={"X-Cutover-Key": "no"}).status_code == 403          # wrong key
    dry = c.post("/admin/vision-cutover/cases/load", json=body, headers=h)
    assert dry.status_code == 200, dry.text
    assert dry.json()["new"] == 2 and dry.json()["counts"]["same job"] == 1 and dry.json()["committed"] is False
    world["db"].rollback()
    assert world["db"].query(VisionCase).filter(VisionCase.run_id == run_id).count() == 0   # dry run wrote nothing
    wrote = c.post("/admin/vision-cutover/cases/load", json={**body, "commit": True}, headers=h)
    assert wrote.status_code == 200 and wrote.json()["new"] == 2 and wrote.json()["committed"] is True
    world["db"].rollback()
    assert world["db"].query(VisionCase).filter(VisionCase.run_id == run_id).count() == 2
    again = c.post("/admin/vision-cutover/cases/load", json={**body, "commit": True}, headers=h)
    assert again.json()["new"] == 0                                            # insert-only: nothing new the second time


def test_cases_load_route_404_for_an_unknown_run(world, monkeypatch):
    monkeypatch.setenv("VISION_CUTOVER_KEY", "test-key")
    r = _client().post("/admin/vision-cutover/cases/load",
                       json={"run_id": 2_000_000_000, "findings": [], "commit": False},
                       headers={"X-Cutover-Key": "test-key"})
    assert r.status_code == 404
