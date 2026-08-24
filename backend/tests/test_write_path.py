"""Unit tests for the migration write-path executor (sync/execute.py).

DB-free: a tiny fake Session + fake model exercise decision-application (INSERT /
UPDATE / ADOPT / SKIP), legacy-key stamping, the id map, the insert-only-fields hook,
the on-insert child hook, and the unresolved-drop path -- none of which need a real
database or the app's ORM.
"""
from scripts.vision_migration.sync.engine import Decision, INSERT, ADOPT, DUP, SKIP
from scripts.vision_migration.sync.execute import execute_domain


class FakeModel:
    """Stand-in ORM row: takes arbitrary kwargs as attributes; gets an id on flush."""

    def __init__(self, **kw):
        self.id = None
        for k, v in kw.items():
            setattr(self, k, v)


class FakeSession:
    """Minimal Session: add/flush assign incrementing ids; get returns seeded rows."""

    def __init__(self, existing=None):
        self._next = 1
        self.added = []
        self._by_id = dict(existing or {})

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next
                self._next += 1
                self._by_id[obj.id] = obj

    def get(self, model, id_):
        return self._by_id.get(id_)


def test_insert_stamps_keys_and_maps_ids():
    session = FakeSession()
    rows = [{"name": "A"}, {"name": "B"}]
    decisions = [Decision(INSERT, "tblX", 10, None, ""), Decision(INSERT, "tblX", 20, None, "")]
    res = execute_domain(session, FakeModel, rows, decisions, to_fields=lambda r: {"name": r["name"]})
    assert res.counts[INSERT] == 2
    assert res.id_map == {10: 1, 20: 2}                 # legacy_id -> new velocity id
    a = session._by_id[1]
    assert a.name == "A" and a.legacy_source == "tblX" and a.legacy_id == 10


def test_adopt_mutates_existing_and_stamps_key():
    existing = FakeModel(name="old", legacy_source=None, legacy_id=None)
    existing.id = 5
    session = FakeSession({5: existing})
    res = execute_domain(session, FakeModel, [{"name": "new"}],
                         [Decision(ADOPT, "tblX", 99, 5, "")],
                         to_fields=lambda r: {"name": r["name"]})
    assert res.counts[ADOPT] == 1 and res.id_map == {99: 5}
    assert existing.name == "new" and existing.legacy_source == "tblX" and existing.legacy_id == 99


def test_skip_and_unresolved_are_not_written():
    session = FakeSession()
    rows = [{"name": "A"}, {"name": "B"}]
    decisions = [Decision(SKIP, "tblX", None, None, ""), Decision(INSERT, "tblX", 7, None, "")]
    res = execute_domain(session, FakeModel, rows, decisions, to_fields=lambda r: None)  # can't map
    assert res.counts[SKIP] == 2 and res.counts[INSERT] == 0
    assert len(res.unresolved) == 1 and res.id_map == {}


def test_insert_extra_only_on_insert_and_on_insert_hook_runs():
    session = FakeSession()
    seen = []
    res = execute_domain(session, FakeModel, [{"name": "A"}], [Decision(INSERT, "tblX", 3, None, "")],
                         to_fields=lambda r: {"name": r["name"]},
                         insert_extra=lambda r: {"seq": 42},
                         on_insert=lambda s, o, r: seen.append(o.id))
    obj = session._by_id[1]
    assert obj.seq == 42 and res.counts[INSERT] == 1    # insert-only field applied
    assert seen == [1]                                  # on_insert ran AFTER flush (id assigned)


def test_dup_resolves_to_claimant_without_second_write():
    """A DUP whose claimant wrote resolves to that row and performs no second write."""
    session = FakeSession()
    rows = [{"name": "A"}, {"name": "A"}]                # two source rows, same real entity
    decisions = [Decision(INSERT, "tblX", 10, None, ""),
                 Decision(DUP, "tblX", 20, None, "", dup_of_legacy_id=10)]
    res = execute_domain(session, FakeModel, rows, decisions, to_fields=lambda r: {"name": r["name"]})
    assert res.counts[INSERT] == 1 and res.counts[DUP] == 1
    assert res.id_map == {10: 1, 20: 1}                 # both legacy ids resolve to the one row
    assert len(session.added) == 1                      # only one write happened


def test_dup_promoted_to_writer_when_claimant_unwritable():
    """The review's finding: a DUP whose claimant fails to write (its FK/field didn't
    resolve -> SKIP) is PROMOTED to the writer itself, so a perfectly writable duplicate
    isn't dropped; the failed claimant's id also resolves to the promoted row so any
    further duplicates still map correctly."""
    session = FakeSession()
    rows = [{"name": "A", "bad": True}, {"name": "A2"}]  # claimant unwritable; duplicate writable
    decisions = [Decision(INSERT, "tblX", 10, None, ""),
                 Decision(DUP, "tblX", 20, None, "", dup_of_legacy_id=10)]
    res = execute_domain(session, FakeModel, rows, decisions,
                         to_fields=lambda r: None if r.get("bad") else {"name": r["name"]})
    assert res.counts[SKIP] == 1 and res.counts[INSERT] == 1 and res.counts[DUP] == 0
    assert res.id_map[20] == 1 and res.id_map[10] == 1  # promoted dup written; failed claimant resolves to it
    assert session._by_id[1].name == "A2" and session._by_id[1].legacy_id == 20


def test_dup_and_claimant_both_unwritable_both_skip():
    """If both the claimant and its duplicate are unwritable, both skip -- no write, no crash."""
    session = FakeSession()
    rows = [{"bad": True}, {"bad": True}]
    decisions = [Decision(INSERT, "tblX", 10, None, ""),
                 Decision(DUP, "tblX", 20, None, "", dup_of_legacy_id=10)]
    res = execute_domain(session, FakeModel, rows, decisions, to_fields=lambda r: None)
    assert res.counts[SKIP] == 2 and res.counts[INSERT] == 0 and res.id_map == {}
