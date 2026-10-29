"""DB-free tests for the batched Vision cutover: the plan, its JSON, the log store and the pre-flight check.

The database writes go through apply.py (tested in test_vision_delta.py and
by the rehearsal on a production copy); here: what goes in which batch,
what is left for the archive, that a plan survives the trip to the API
exactly, and that the API refuses a batch it has no room for.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from scripts.vision_delta import apply as ap
from scripts.vision_delta import compare as cp
from scripts.vision_delta import plan as pl
from scripts.vision_delta import runner as rn


# ---- JSON round trip ---------------------------------------------------------

def test_encode_decode_keeps_dates_times_and_money_exact():
    data = {"when": datetime(2026, 10, 29, 12, 3, 52), "day": date(2018, 1, 1), "price": Decimal("12.345"),
            "pair": ("a", 1), "none": None, "rows": [{"q": -2.5}]}
    back = rn.decode(__import__("json").dumps(rn.encode(data)))
    assert back["when"] == data["when"] and back["day"] == data["day"] and back["price"] == Decimal("12.345")
    assert back["pair"] == ["a", 1] and back["rows"] == [{"q": -2.5}] and back["none"] is None


def test_encode_refuses_what_it_cannot_restore():
    with pytest.raises(TypeError):
        rn.encode({("kind", 1): 2})                        # tuple keys: links travel as lists instead
    with pytest.raises(TypeError):
        rn.encode(object())


def test_delta_of_rebuilds_links_and_pairs():
    batch = {"rows": [], "links": [["part", 5, 50]], "project_map": {"A1": "A1"},
             "quote_pairs": [[{"wo": 1}, {"id": 2}]], "po_pairs": [], "project_pairs": []}
    d = rn.delta_of(batch)
    assert d.links == {("part", 5): 50} and d.quote_pairs == [({"wo": 1}, {"id": 2})]


# ---- the plan: activity, archive, batches -----------------------------------

def test_last_activity_takes_the_newest_vision_date_under_the_rows_label():
    v = {"projects": [{"uca": "A1", "created_on": date(2010, 1, 1)}, {"uca": "A2", "created_on": date(2012, 1, 1)}],
         "quotes": [{"uca": "A9", "vision_uca": "A1", "created_at": datetime(2015, 5, 1), "entered": None,
                     "revised": None, "lines": [{"entered": date(2019, 3, 1)}]}],
         "pos": [{"uca": "A2", "vision_uca": "A2", "created_at": None}]}
    act = pl.last_activity(v, {"A1": "A9", "A2": "vision A2"})
    assert act == {"A9": date(2019, 3, 1), "vision A2": date(2012, 1, 1)}


def _r(level, kind, project, vk=None, tk=None, field="", ref=None, why=cp.JUNE_RULE):
    return {"level": level, "kind": kind, "project": project, "vision_key": vk, "velocity_key": tk, "context": "",
            "field": field, "vision_value": 1, "velocity_value": 0, "cause": why, "_ref": ref}


def _plan_inputs():
    """A small compare: catalogue, an active project, an old one, a new project keeping its number, one renumbered."""
    d = cp.Delta()
    d.rows = [
        _r("parts", cp.MISSING, "", vk=1, ref={"vision": {"vid": 1}}),
        _r("quote lines", cp.DIFFERS, "A1", vk=11, tk=110, field="qty_pending",
           ref={"vision": {"item_vid": 7}, "parent": 100}),
        _r("quote lines", cp.DIFFERS, "A0", vk=12, tk=120, field="qty_pending",
           ref={"vision": {"item_vid": 8}, "parent": 200}),
        _r("projects", cp.MISSING, "vision A5", vk=5, ref={"vision": {"uca": "A5"}, "new_number": True}),
        _r("projects", cp.MISSING, "A6", vk=6, ref={"vision": {"uca": "A6"}, "new_number": False}),
        _r("quotes", cp.EXTRA, "A1", tk=101),          # only in Velocity: never planned
    ]
    d.links = {("part", 7): 70, ("labor", 8): 80, ("misc", 9): 90}
    d.project_map = {"A1": "A1", "A0": "A0", "A5": "vision A5", "A6": "A6"}
    keyed = {"legacy_id": 1, "vision_wo_number": None, "vision_closed": None, "status": "Draft", "lines": [],
             "client_po_number": None}
    d.quote_pairs = [({"wo": 1, "status": "Draft"}, {"id": 100, "uca": "A1", "quote_number": "A1-0001-0", **keyed}),
                     ({"wo": 2, "status": "Draft"}, {"id": 200, "uca": "A0", "quote_number": "A0-0001-0", **keyed}),
                     ({"wo": 3, "status": "Draft"}, {"id": 300, "uca": "A1", "quote_number": "A1-0002-0", **keyed})]
    v = {"projects": [{"uca": "A1", "created_on": date(2024, 1, 1)}, {"uca": "A0", "created_on": date(2009, 1, 1)},
                      {"uca": "A5", "created_on": date(2025, 1, 1)}, {"uca": "A6", "created_on": date(2025, 2, 1)}],
         "quotes": [], "pos": []}
    return v, d


def test_build_archives_old_projects_and_orders_batches():
    v, d = _plan_inputs()
    batches, left, stats = pl.build(v, d, {}, None, date(2018, 1, 1), batch_rows=1)
    assert [b["projects"] for b in batches] == [[""], ["A6"], ["A1"], ["vision A5"]]   # catalogue, kept, rest, renumbered
    archived = [r for r in left if r["reason"].startswith(pl.ARCHIVE)]
    assert [(r["project"], r["vision_key"]) for r in archived] == [("A0", 12)]
    assert stats["archived rows"] == 1 and stats["batches"] == 4
    a1 = batches[2]
    assert a1["links"] == [["part", 7, 70]]                 # only the item links its lines use
    assert [t["id"] for _, t in a1["quote_pairs"]] == [100]  # the quote its rows change; 300 has nothing to do
    assert all("_ref" not in r for r in left)


def test_build_packs_small_projects_but_never_with_the_catalogue():
    v, d = _plan_inputs()
    batches, _, _ = pl.build(v, d, {}, None, date(2018, 1, 1), batch_rows=100)
    assert [b["projects"] for b in batches] == [[""], ["A6", "A1", "vision A5"]]


def test_plan_hash_ignores_estimates():
    v, d = _plan_inputs()
    batches, _, _ = pl.build(v, d, {}, None, date(2018, 1, 1))
    h = pl.plan_hash(batches)
    batches[0]["estimate"] = {"wal_bytes": 1}
    batches[0]["plan"] = h
    assert pl.plan_hash(batches) == h
    batches[0]["rows"] = []
    assert pl.plan_hash(batches) != h


def test_stamp_plan_skips_numbers_on_records_the_june_import_keyed():
    _, d = _plan_inputs()
    d.quote_pairs = [({"wo": 1, "status": "Closed"},
                      {"id": 100, "uca": "A1", "quote_number": "A1-0001-0", "legacy_id": 1, "vision_wo_number": None,
                       "vision_closed": None, "status": "Draft", "client_po_number": None,
                       "lines": [{"qty_fulfilled": 0, "qty_pending": 0}]})]
    updates, _ = ap.stamp_plan(d)
    assert updates[0][1] == [{"id": 100, "vision_closed": True, "status": "Closed"}]   # the flag, no number


# ---- pre-flight ---------------------------------------------------------------

NOW = {"db_mb": 60.0, "wal_dir_mb": 290.0, "wal_since_checkpoint_mb": 20.0, "archive_mode": "on", "archive_ready": 0}
LIMITS = {"wal_budget_mb": 128.0, "disk_limit_mb": 650.0, "archive_ready_max": 8}


def test_over_budget_lets_a_small_batch_through():
    assert rn.over_budget(NOW, {"wal_bytes": 10 * rn.MB, "db_bytes": 2 * rn.MB}, LIMITS) == []


@pytest.mark.parametrize("now, estimate, words", [
    (NOW, None, "not measured"),
    ({**NOW, "wal_since_checkpoint_mb": 120.0}, {"wal_bytes": 10 * rn.MB, "db_bytes": 0}, "wait for the next checkpoint"),
    ({**NOW, "archive_ready": 9}, {"wal_bytes": rn.MB, "db_bytes": 0}, "archiver"),
    ({**NOW, "db_mb": 340.0}, {"wal_bytes": 10 * rn.MB, "db_bytes": 10 * rn.MB}, "disk limit"),
])
def test_over_budget_names_why_a_batch_waits(now, estimate, words):
    assert any(words in why for why in rn.over_budget(now, estimate, LIMITS))


# ---- log store and the API's key ------------------------------------------------

def test_folder_store_round_trip_and_refuses_keys_outside_it(tmp_path):
    from log_store import FolderStore
    s = FolderStore(str(tmp_path))
    s.put_json("vision-cutover/run-1/batch-0001.json", [{"a": 1}])
    assert s.get_json("vision-cutover/run-1/batch-0001.json") == [{"a": 1}]
    s.delete("vision-cutover/run-1/batch-0001.json")
    with pytest.raises(KeyError):
        s.get_json("vision-cutover/run-1/batch-0001.json")
    with pytest.raises(ValueError):
        s.put_json("../outside.json", {})


def test_log_store_from_env(monkeypatch, tmp_path):
    import log_store
    for k in ("BUCKET", "LOG_STORE_DIR", "ENDPOINT", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert log_store.from_env() is None
    monkeypatch.setenv("LOG_STORE_DIR", str(tmp_path))
    assert isinstance(log_store.from_env(), log_store.FolderStore)
    monkeypatch.setenv("BUCKET", "b")
    with pytest.raises(RuntimeError):
        log_store.from_env()                               # a bucket without its credentials


def test_cutover_api_is_off_without_a_key_and_refuses_a_wrong_one(monkeypatch):
    from fastapi import HTTPException
    from routes.vision_cutover import require_key
    monkeypatch.delenv("VISION_CUTOVER_KEY", raising=False)
    with pytest.raises(HTTPException) as e:
        require_key("anything")
    assert e.value.status_code == 404
    monkeypatch.setenv("VISION_CUTOVER_KEY", "s3cret")
    with pytest.raises(HTTPException) as e:
        require_key("wrong")
    assert e.value.status_code == 403
    assert require_key("s3cret") is None
