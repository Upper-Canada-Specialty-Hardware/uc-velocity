"""Unit tests for the Vision -> Velocity import engine's decision logic.

These exercise the pure ``decide`` function with plain dict fixtures -- no
database -- proving each source row is routed to exactly one of update / adopt /
insert / skip, and that an existing un-keyed row is never adopted twice.
"""
from scripts.vision_migration.sync import engine as e
from scripts.vision_migration.sync.engine import DomainSpec, INSERT, UPDATE, ADOPT, DUP, SKIP


# A fake domain: legacy id under "legacy_id", natural key under "nat".
SPEC = DomainSpec(
    legacy_source="tblServiceRecords",
    legacy_id_of=lambda r: r.get("legacy_id"),
    natural_key_of=lambda r: r.get("nat"),
)


def _one(row, existing_by_legacy=None, existing_by_natural=None):
    """Run one source row through the engine and return its single Decision."""
    return e.decide([row], SPEC, existing_by_legacy or {}, existing_by_natural or {})[0]


def test_occurrence_keys_numbers_identical_keys_in_order():
    """Identical keys become (key, 1), (key, 2)...; None stays None; order is kept."""
    keys = [("q1", "part", "Widget", 2, 9.99), None, ("q1", "part", "Widget", 2, 9.99), ("q1", "misc", "Travel", 1, 50.0)]
    assert e.occurrence_keys(keys) == [
        (("q1", "part", "Widget", 2, 9.99), 1), None,
        (("q1", "part", "Widget", 2, 9.99), 2), (("q1", "misc", "Travel", 1, 50.0), 1),
    ]


def test_identical_child_lines_adopt_distinct_existing_rows_not_dup():
    """Two byte-identical lines on one quote are two entities: with occurrence keys the
    first source line adopts the first existing line and the second adopts the second,
    instead of the second becoming a DUP (which is right for parts, wrong for lines)."""
    content = ("quote7", "part", "Push plate", 2, 172.0)
    existing_by_natural = {(content, 1): 501, (content, 2): 502}           # two existing un-keyed lines
    rows = [{"legacy_id": 9001, "nat": (content, 1)}, {"legacy_id": 9002, "nat": (content, 2)}]
    d1, d2 = e.decide(rows, SPEC, {}, existing_by_natural)
    assert (d1.action, d1.target_id) == (ADOPT, 501)
    assert (d2.action, d2.target_id) == (ADOPT, 502)


def test_third_identical_line_with_no_existing_counterpart_inserts():
    """One more source line than existing lines -> the extra one is a genuine INSERT."""
    content = ("quote7", "part", "Push plate", 2, 172.0)
    rows = [{"legacy_id": 1, "nat": (content, 1)}, {"legacy_id": 2, "nat": (content, 2)}]
    d1, d2 = e.decide(rows, SPEC, {}, {(content, 1): 501})
    assert (d1.action, d1.target_id) == (ADOPT, 501)
    assert d2.action == INSERT


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


def test_duplicate_of_adopted_row_resolves_to_it_not_a_second_write():
    """Two source rows sharing a natural key that matches an existing un-keyed row:
    the first adopts it; the second is a DUP that points back at the first (so both
    Vision ids resolve to the one Velocity row -- never a second write)."""
    rows = [{"legacy_id": 5, "nat": "WO5"}, {"legacy_id": 6, "nat": "WO5"}]
    decisions = e.decide(rows, SPEC, {}, existing_by_natural={"WO5": 200})
    assert decisions[0].action == ADOPT and decisions[0].target_id == 200
    assert decisions[1].action == DUP
    assert decisions[1].dup_of_legacy_id == 5  # resolves to the first row, not a 2nd insert


def test_duplicate_natural_key_among_new_rows_dedups_within_run():
    """Two genuinely-new source rows sharing a natural key (nothing pre-existing): the
    first inserts; the second is a DUP of it. This is the parts case -- a part number
    listed twice (e.g. ' 10PBR1' and '10PBR1') must not INSERT twice into a UNIQUE column."""
    rows = [{"legacy_id": 5, "nat": "10PBR1"}, {"legacy_id": 6, "nat": "10PBR1"}]
    decisions = e.decide(rows, SPEC, {}, {})
    assert decisions[0].action == INSERT
    assert decisions[1].action == DUP and decisions[1].dup_of_legacy_id == 5


def test_null_natural_keys_never_dedup():
    """Rows whose natural key is None can't be matched to each other -> each inserts."""
    rows = [{"legacy_id": 5, "nat": None}, {"legacy_id": 6, "nat": None}]
    decisions = e.decide(rows, SPEC, {}, {})
    assert decisions[0].action == INSERT and decisions[1].action == INSERT


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
    assert counts == {UPDATE: 1, ADOPT: 1, INSERT: 1, DUP: 0, SKIP: 1}
