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
    made = {"db": db, "project": proj, "quote": q, "line": li, "run": run}
    yield made
    db.rollback()
    db.query(VisionCase).filter(VisionCase.run_id == run.id).delete()
    snaps = [s.id for s in db.query(QuoteSnapshot).filter(QuoteSnapshot.quote_id == q.id)]
    db.query(QuoteLineItemSnapshot).filter(QuoteLineItemSnapshot.snapshot_id.in_(snaps)).delete(synchronize_session=False)
    db.query(QuoteSnapshot).filter(QuoteSnapshot.quote_id == q.id).delete()
    db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == q.id).delete()
    db.query(Quote).filter(Quote.id == q.id).delete()
    db.query(Project).filter(Project.id == proj.id).delete()
    db.query(Profile).filter(Profile.id == cust.id).delete()
    db.query(VisionSyncRun).filter(VisionSyncRun.id == run.id).delete()
    db.commit()
    db.close()


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


def test_same_job_records_the_vision_wo_on_the_velocity_quote(world):
    found = {"vision WO": "13635", "velocity quote id": str(world["quote"].id)}
    c = _case(world, kind="same job", prediction="same job", found=found)
    r = _client().post(f"/vision-decisions/cases/{c.id}/decide", json={"decision": "same job"})
    assert r.status_code == 200, r.text
    world["db"].expire_all()
    assert world["quote"].vision_wo_number == 13635


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
