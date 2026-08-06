"""Unit tests for the Vision -> Velocity import engine's decision logic.

These exercise the pure ``decide`` function with plain dict fixtures -- no
database -- proving each source row is routed to exactly one of update / adopt /
insert / skip, and that an existing un-keyed row is never adopted twice.
"""
from scripts.vision_migration.sync import engine as e
from scripts.vision_migration.sync.engine import DomainSpec, INSERT, UPDATE, ADOPT, SKIP


# A fake domain: legacy id under "legacy_id", natural key under "nat".
SPEC = DomainSpec(
    legacy_source="tblServiceRecords",
    legacy_id_of=lambda r: r.get("legacy_id"),
    natural_key_of=lambda r: r.get("nat"),
)


def _one(row, existing_by_legacy=None, existing_by_natural=None):
    """Run one source row through the engine and return its single Decision."""
    return e.decide([row], SPEC, existing_by_legacy or {}, existing_by_natural or {})[0]


def test_update_when_stored_legacy_key_matches():
    """A row whose (source, id) already exists in Velocity is refreshed in place."""
    d = _one({"legacy_id": 5, "nat": "WO5"}, existing_by_legacy={("tblServiceRecords", 5): 100})
    assert d.action == UPDATE
    assert d.target_id == 100


def test_adopt_when_only_natural_key_matches():
    """No stored key, but the natural key matches an existing un-keyed row -> adopt it."""
    d = _one({"legacy_id": 5, "nat": "WO5"}, existing_by_natural={"WO5": 200})
    assert d.action == ADOPT
    assert d.target_id == 200


def test_insert_when_nothing_matches():
    """Neither key matches -> the row is genuinely new."""
    d = _one({"legacy_id": 5, "nat": "WO5"})
    assert d.action == INSERT
    assert d.target_id is None


def test_skip_when_no_legacy_id():
    """A source row with no Vision id can't be keyed or matched, so it's left alone."""
    d = _one({"legacy_id": None, "nat": "WO5"}, existing_by_natural={"WO5": 200})
    assert d.action == SKIP


def test_stored_key_wins_over_natural_key():
    """When both could match, the stored legacy key (the reliable one) takes precedence."""
    d = _one(
        {"legacy_id": 5, "nat": "WO5"},
        existing_by_legacy={("tblServiceRecords", 5): 100},
        existing_by_natural={"WO5": 200},
    )
    assert d.action == UPDATE
    assert d.target_id == 100


def test_natural_key_adopted_only_once():
    """Two source rows sharing a natural key: the first adopts the row, the rest insert."""
    rows = [{"legacy_id": 5, "nat": "WO5"}, {"legacy_id": 6, "nat": "WO5"}]
    decisions = e.decide(rows, SPEC, {}, existing_by_natural={"WO5": 200})
    assert decisions[0].action == ADOPT and decisions[0].target_id == 200
    assert decisions[1].action == INSERT  # can't claim the same existing row twice


def test_summarize_tallies_all_actions():
    """The dry-run ledger counts every action, including zeros for unused ones."""
    rows = [
        {"legacy_id": 5, "nat": "a"},   # update
        {"legacy_id": 6, "nat": "b"},   # adopt
        {"legacy_id": 7, "nat": "c"},   # insert
        {"legacy_id": None, "nat": "d"},  # skip
    ]
    decisions = e.decide(
        rows, SPEC,
        existing_by_legacy={("tblServiceRecords", 5): 100},
        existing_by_natural={"b": 200},
    )
    counts = e.summarize(decisions)
    assert counts == {UPDATE: 1, ADOPT: 1, INSERT: 1, SKIP: 1}
