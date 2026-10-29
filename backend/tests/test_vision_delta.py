"""DB-free tests for the Vision -> Velocity delta tools (scripts/vision_delta).

Covers the pure parts: pairing (projects by number and name, quotes, POs,
lines), causes, item links, look-alike evidence, the per-project review, the
app's status rule with the Vision-closed flag, and the update tool's default
rule, decisions file, line checks and Vision-number plan. The database writes
are exercised by a rehearsal run on a production copy and checked by verify.py.
"""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from scripts.vision_delta import apply as ap
from scripts.vision_delta import compare as cp
from routes.quotes import compute_status_from_lines


# ---- causes ----------------------------------------------------------------

@pytest.mark.parametrize("vision_later, velocity_edit, june, expected", [
    (True, True, None, cp.BOTH),                           # both sides moved: a person decides
    (False, True, cp.JUNE_RULE, cp.VELOCITY_EDIT),         # a Velocity edit outranks the June explanation
    (True, False, cp.JUNE_RULE, cp.VISION_LATER),          # a later Vision change outranks it too
    (False, False, cp.JUNE_ROUNDING, cp.JUNE_ROUNDING),
    (False, False, None, cp.UNEXPLAINED),
])
def test_cause(vision_later, velocity_edit, june, expected):
    assert cp.cause(vision_later, velocity_edit, june) == expected


def test_june_quantity_rounds_to_at_least_one():
    assert [cp.june_quantity(q) for q in (0, 0.4, 1.5, 2.5, 3.6)] == [1, 1, 2, 2, 4]   # Python round(): halves to even
    assert cp.same_quantity(0.5, 1) and cp.same_quantity(1.25, 1.25) and not cp.same_quantity(2, 3)


# ---- pairing ----------------------------------------------------------------

def test_pair_quotes_lowest_id_is_original_rest_are_clones():
    v = [{"wo": 10}, {"wo": 11}]
    t = [{"id": 5, "wo": 10}, {"id": 3, "wo": 10}, {"id": 9, "wo": None, "legacy_id": None}]
    pairs, missing, extra, clones = cp.pair_quotes(v, t)
    assert [(a["wo"], b["id"]) for a, b in pairs] == [(10, 3)]          # id 3 was imported first
    assert [m["wo"] for m in missing] == [11]
    assert [e["id"] for e in extra] == [9]                              # made in Velocity
    assert [(o["id"], c["id"]) for o, c in clones] == [(3, 5)]


def test_pair_pos_replays_june_numbering_only_when_vendor_and_date_agree():
    d = datetime(2025, 3, 1)
    v = [{"vid": 1, "uca": "A1", "seq": 1, "vendor": "Acme", "created_at": d},
         {"vid": 2, "uca": "A1", "seq": 2, "vendor": "Bolt", "created_at": d}]
    t = [{"id": 7, "uca": "A1", "seq": 1, "vendor": "ACME ", "created_at": d},   # same vendor, loose case
         {"id": 8, "uca": "A1", "seq": 2, "vendor": "Other", "created_at": d}]   # number matches, vendor does not
    pairs, missing, extra = cp.pair_pos(v, t)
    assert [(a["vid"], b["id"]) for a, b in pairs] == [(1, 7)]
    assert [m["vid"] for m in missing] == [2] and [e["id"] for e in extra] == [8]


def test_pair_lines_prefers_matching_values_then_order_then_kind():
    key = lambda l: ("name", l["ref"])
    same = lambda a, b: a["qty"] == b["qty"]
    v = [{"vid": 1, "k": "part", "ref": "X", "qty": 2}, {"vid": 2, "k": "part", "ref": "X", "qty": 5},
         {"vid": 3, "k": "labor", "ref": "L", "qty": 1}]
    t = [{"id": 10, "k": "part", "ref": "X", "qty": 5}, {"id": 11, "k": "part", "ref": "X", "qty": 9},
         {"id": 12, "k": "labor", "ref": "M", "qty": 1}]
    pairs, missing, extra = cp.pair_lines(v, t, lambda l: l["k"], key, key, same)
    got = sorted((a["vid"], b["id"]) for a, b in pairs)
    assert got == [(1, 11), (2, 10), (3, 12)]              # 2<->10 by value, 1<->11 by order, 3<->12 by kind
    assert not missing and not extra


def test_infer_item_links_needs_a_clear_majority():
    line = lambda item, desc: {"item_vid": item, "item_id": None, "item_type": "part", "description": desc,
                               "unit_price": 1.0, "quantity": 1}
    tline = lambda item, desc: {"item_id": item, "item_type": "part", "description": desc, "unit_price": 1.0, "quantity": 1}
    pairs = [({"lines": [line(100, "bolt")]}, {"lines": [tline(7, "bolt")]}),
             ({"lines": [line(100, "bolt")]}, {"lines": [tline(7, "bolt")]}),
             ({"lines": [line(200, "nut")]}, {"lines": [tline(8, "nut"), tline(9, "nut")]})]   # ambiguous: no vote
    assert cp.infer_item_links(pairs, lambda l: l["item_type"]) == {("part", 100): 7}


# ---- per-project review -----------------------------------------------------

def test_project_review_counts_records_not_fields_and_filters_by_date():
    d = cp.Delta()
    d.add("quotes", cp.DIFFERS, 1, 50, field="status", project="A2")
    d.add("quotes", cp.DIFFERS, 1, 50, field="created_at", project="A2")
    d.add("quotes", cp.DIFFERS, 1, 50, field="work_description", project="A2")   # same quote: counts once
    d.add("quotes", cp.MISSING, 2, None, cause_="possible double entry: A2-0003-1", project="A2")
    v = {"projects": [{"vid": 1, "uca": "A1", "name": "old", "customer": "c", "status": "active",
                       "created_on": datetime(2024, 5, 1)},
                      {"vid": 2, "uca": "A2", "name": "new", "customer": "c", "status": "active",
                       "created_on": datetime(2025, 2, 1)}],
         "quotes": [{"uca": "A2"}, {"uca": "A2"}], "pos": []}
    t = {"projects": [{"id": 1, "uca": "A1", "name": "old", "customer": "c", "created_on": datetime(2024, 5, 1)},
                      {"id": 2, "uca": "A2", "name": "new", "customer": "c", "created_on": datetime(2025, 2, 3)},
                      {"id": 3, "uca": "A3", "name": "v only", "customer": "c", "created_on": datetime(2026, 1, 1)}],
         "quotes": [{"uca": "A2"}], "pos": []}
    review = cp.project_review(d, v, t, date(2025, 1, 1))
    assert [r["project"] for r in review] == ["A2", "A3"]   # A1 predates the cut-off
    a2, a3 = review
    assert a2["quotes with a different status"] == 1 and a2["quotes with other header differences"] == 1
    assert a2["quotes missing in velocity"] == 1 and a2["missing work orders"] == "2"
    assert a2["notes"] == "possible double entry: A2-0003-1"
    assert (a2["vision quotes"], a2["velocity quotes"]) == (2, 1)
    assert a3["in velocity"] == "velocity only" and a3["vision date created"] is None


# ---- update tool: default rule ----------------------------------------------

def _row(level="quote lines", kind=cp.DIFFERS, field="qty_pending", why=cp.JUNE_RULE, vk=1, tk=2):
    return {"level": level, "kind": kind, "field": field, "cause": why, "vision_key": vk, "velocity_key": tk}


@pytest.mark.parametrize("row, outcome, reason_part", [
    (_row(kind=cp.EXTRA), ap.SKIPPED, "never touched"),
    (_row(kind=cp.COPY), ap.SKIPPED, "never touched"),
    (_row(level="parts", kind=cp.MISSING, field="", why=""), ap.APPLIED, "missing"),
    (_row(level="quotes", kind=cp.MISSING, field="", why=""), ap.APPLIED, "missing"),   # a look-alike never holds it back
    (_row(level="projects", field="uca", why=cp.RENUMBERED), ap.INFO, "velocity number kept"),
    (_row(field="item", why=cp.UNEXPLAINED), ap.SKIPPED, "different inventory item"),
    (_row(level="quotes", field="uca", why=cp.VISION_LATER), ap.SKIPPED, "another project"),
    (_row(level="quotes", field="status"), ap.INFO, "follows"),
    (_row(why=cp.JUNE_ROUNDING), ap.APPLIED, cp.JUNE_ROUNDING),
    (_row(why=cp.VISION_LATER), ap.APPLIED, cp.VISION_LATER),
    (_row(why=cp.VELOCITY_EDIT), ap.SKIPPED, "velocity kept"),
    (_row(why=cp.BOTH), ap.SKIPPED, ap.NEEDS),
    (_row(why=cp.UNEXPLAINED), ap.SKIPPED, ap.NEEDS),
    (_row(kind=cp.MISSING, field="", why=cp.UNEXPLAINED), ap.SKIPPED, ap.NEEDS),   # a missing line is not inserted blindly
])
def test_rule(row, outcome, reason_part):
    got, reason = ap.rule(row)
    assert got == outcome and reason_part in reason


# ---- update tool: decisions file --------------------------------------------

def test_decisions_most_specific_match_wins(tmp_path):
    f = tmp_path / "decisions.csv"
    f.write_text("level,vision_key,velocity_key,field,decision\n"
                 "quotes,13700,,,apply\n"                   # whole work order
                 "quote lines,5,9,quantity,skip\n"
                 "quote lines,5,9,,apply\n"
                 "parts,,,,\n", encoding="utf-8-sig")       # blank decision: ignored
    d = ap.load_decisions(str(f))
    assert ap.decided(d, _row(level="quotes", kind=cp.MISSING, field="", vk=13700, tk=None)) == "apply"
    assert ap.decided(d, _row(field="quantity", vk=5, tk=9)) == "skip"
    assert ap.decided(d, _row(field="unit_price", vk=5, tk=9)) == "apply"
    assert ap.decided(d, _row(level="parts", vk=1)) is None


def test_decisions_reject_unknown_words(tmp_path):
    f = tmp_path / "decisions.csv"
    f.write_text("level,vision_key,velocity_key,field,decision\nquotes,1,,,maybe\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ap.load_decisions(str(f))


# ---- update tool: what a line may hold --------------------------------------

@pytest.mark.parametrize("item_type, qty, done, left, ok", [
    ("labor", 1.5, 0.5, 1.0, True),                        # fractional labour is fine
    ("part", 2, 2, 0, True),
    ("part", 1.5, 0, 1.5, True),                           # stored as Vision has it
    ("misc", 0, 0, 0, True),                               # zero: quoted, not ordered
    ("misc", -1, -1, 0, True),                             # a processed credit
    ("misc", -1, 0, 0, True),                              # a credit not yet processed
    ("misc", -1, 0, -1, False),                            # a credit is never pending
    ("misc", -2, -1, 0, False),                            # nor half processed
    ("labor", 3, 1, 1, False),                             # does not add up
    ("labor", 3, -1, 4, False),                            # nothing below zero on a positive line
])
def test_line_problem(item_type, qty, done, left, ok):
    assert (ap.Writer.line_problem(item_type, qty, done, left) is None) is ok


@pytest.mark.parametrize("qty, received, pending, ok", [
    (5, 2, 3, True), (5, 5, 0, True), (0, 0, 0, True), (5, 0, 6, False), (5, -1, 6, False), (-1, 0, 0, False),
])
def test_po_line_problem(qty, received, pending, ok):
    assert (ap.Writer.po_line_problem(qty, received, pending) is None) is ok


# ---- the app's status rule: the Vision-closed flag ---------------------------

def _ln(done, left):
    return SimpleNamespace(qty_fulfilled=done, qty_pending=left)


@pytest.mark.parametrize("lines, flag, expected", [
    ([_ln(0, 0)], True, "Closed"),                         # zero-quantity job Vision finished
    ([_ln(-1, 0)], True, "Closed"),                        # credit-only job Vision finished
    ([], True, "Closed"),                                  # finished with no lines
    ([_ln(0, 0)], None, "Work Order"),                     # without the flag: the plain rule
    ([_ln(1, 0), _ln(0, 2)], True, "Invoiced"),            # something pending again: the plain rule
    ([_ln(2, 0)], None, "Closed"),
])
def test_status_rule_with_vision_closed(lines, flag, expected):
    assert compute_status_from_lines(lines, "PO-1", flag) == expected


def test_vision_closed_only_from_a_closed_vision_status():
    assert ap.vision_closed({"status": "Closed"}) is True
    assert ap.vision_closed({"status": "Invoiced"}) is None   # never False: null means "not said"


# ---- projects: same number, different project --------------------------------

def test_name_similarity_tolerates_typos_not_other_projects():
    assert cp.name_similarity("Correctional Center", "Correctional  Centr.") >= cp.SAME_NAME
    assert cp.name_similarity("SHN", "Peterborough Hospital") < cp.SAME_NAME
    assert cp.name_similarity(None, "") == 1.0 and cp.name_similarity("A", None) == 0.0


def _vp(vid, uca, name, customer="C"):
    return {"vid": vid, "uca": uca, "name": name, "customer": customer}


def _tp(id_, uca, name, customer="C", legacy_id=None):
    return {"id": id_, "uca": uca, "name": name, "customer": customer, "legacy_id": legacy_id}


def test_pair_projects_by_stamp_then_number_and_name_then_name_alone():
    vision = [_vp(1, "A1", "Alpha"), _vp(2, "A2", "Bravo"), _vp(3, "A3", "Charlie"), _vp(4, "A4", "Delta")]
    velocity = [_tp(10, "A9", "renamed", legacy_id=1),     # stamped: wins over everything
                _tp(20, "A2", "Bravo Site"),               # same number, similar name
                _tp(30, "A3", "Zulu"),                     # same number, another project
                _tp(40, "A7", "Charlie"),                  # Charlie under its Velocity number
                _tp(50, "A8", "Delta"), _tp(60, "A9b", "Delta")]   # two candidates: neither
    pairs, missing, extra = cp.pair_projects(vision, velocity)
    got = {v["uca"]: t["id"] for v, t in pairs}
    assert got == {"A1": 10, "A2": 20, "A3": 40}
    assert [v["uca"] for v in missing] == ["A4"]
    assert sorted(t["id"] for t in extra) == [30, 50, 60]


def test_relabel_moves_records_to_the_velocity_number_and_keeps_vision_number():
    v = {"quotes": [{"uca": "A3"}, {"uca": "A4"}], "pos": [{"uca": "A5"}]}
    cp.relabel(v, {"A3": "A7", "A4": "vision A4"})
    cp.relabel(v, {"A3": "A7", "A4": "vision A4"})          # a second call changes nothing
    assert [(q["vision_uca"], q["uca"]) for q in v["quotes"]] == [("A3", "A7"), ("A4", "vision A4")]
    assert (v["pos"][0]["vision_uca"], v["pos"][0]["uca"]) == ("A5", "A5")


# ---- look-alikes: evidence only ----------------------------------------------

def _wo(wo, day, refs, text=""):
    return {"wo": wo, "created_at": datetime(2026, 9, day), "work_description": text,
            "lines": [{"item_type": "part", "ref": r} for r in refs]}


def _q(number, day, refs, text=""):
    return {"quote_number": number, "created_at": datetime(2026, 9, day), "work_description": text,
            "lines": [{"item_type": "part", "ref": r} for r in refs]}


def test_twin_evidence():
    v = _wo(13637, 3, ["P1", "P2", "P3"])
    assert cp.twin_evidence(v, _q("A1-0001-3", 3, ["P1", "P2"], "see vision WO 13637")) == \
        ["cross-reference", "same day", "2 of 3 items shared"]
    assert cp.twin_evidence(v, _q("A1-0002-0", 4, ["P1"], "[WO 13637] imported tag")) == []   # own tag, 1 of 3
    assert cp.twin_evidence(_wo(1, 3, ["P1"]), _q("A1-0003-0", 9, ["P1"])) == []              # one item says little


# ---- Vision numbers on paired records ----------------------------------------

def test_stamp_plan_fills_only_what_is_missing():
    from models import Project, PurchaseOrder, Quote
    d = cp.Delta()
    closed_lines = [{"qty_fulfilled": 0, "qty_pending": 0}]
    d.quote_pairs = [
        ({"wo": 7, "status": "Closed"}, {"id": 1, "uca": "A1", "quote_number": "A1-0001-0", "lines": closed_lines,
                                         "client_po_number": None, "status": "Draft", "legacy_id": None,
                                         "vision_wo_number": None, "vision_closed": None}),
        ({"wo": 8, "status": "Draft"}, {"id": 2, "uca": "A1", "quote_number": "A1-0002-0", "lines": [],
                                        "client_po_number": None, "status": "Draft", "legacy_id": 8,
                                        "vision_wo_number": 8, "vision_closed": None}),   # already done
    ]
    d.po_pairs = [({"vid": 5, "number": None}, {"id": 3, "uca": "A1", "po_number": "PO-A1-0001-0", "legacy_id": 99,
                                                 "vision_po_number": None})]               # keyed, no number: nothing
    d.project_pairs = [({"vid": 4, "uca": "A2"}, {"id": 9, "uca": "A1", "name": "Alpha", "legacy_id": None,
                                                   "vision_project_number": None})]
    updates, report = ap.stamp_plan(d)
    rows = {model: batch for model, batch in updates}
    assert rows[Quote] == [{"id": 1, "vision_wo_number": 7, "legacy_id": 7, "legacy_source": "tblServiceRecords",
                            "vision_closed": True, "status": "Closed"}]
    assert PurchaseOrder not in rows
    assert rows[Project] == [{"id": 9, "vision_project_number": "A2", "legacy_id": 4, "legacy_source": "tblProjects"}]
    assert {(r["record_id"], r["field"]) for r in report} == {
        (1, "status"), (1, "vision_wo_number"), (1, "legacy_id"), (1, "vision_closed"),
        (9, "vision_project_number"), (9, "legacy_id")}
    assert ap.stamp_plan(d, {1})[0][0][1][0].get("status") is None   # its lines were rewritten: status set there
