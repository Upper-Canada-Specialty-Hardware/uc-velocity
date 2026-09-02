"""Regression test for issue #230: cloning (and reverting) a quote must keep the
per-line markup.

`getLineItemUnitPrice` prefers ``base_cost * (1 + markup_percent/100)`` and only
falls back to the stored ``unit_price`` when ``markup_percent`` is missing. Clone
and revert rebuilt each line without ``markup_percent``, so the clone/revert lost
its markup and froze at ``unit_price``. These tests pin the field through both paths.

DB-dependent: skipped (via conftest) when Postgres is unreachable, like the other
DB tests; CI runs it against its Postgres.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
from database import SessionLocal
from models import (
    Profile, ProfileType, Project, Quote, QuoteLineItem,
    QuoteSnapshot, QuoteLineItemSnapshot,
)

client = TestClient(main.app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _suffix():
    return datetime.utcnow().strftime("%H%M%S%f")


def _make_quote_with_markup_line(db, *, sequence=1, current_version=0):
    """Create a customer, project, and a single-line quote whose line carries a
    per-line markup (control OFF). Returns (customer, project, quote, line)."""
    suffix = _suffix()
    customer = Profile(name=f"[TEST] Cust {suffix}", type=ProfileType.customer,
                       pst="PST-TEST", address="1 Test St", postal_code="A1A1A1")
    db.add(customer)
    db.flush()
    project = Project(name=f"[TEST] Proj {suffix}", customer_id=customer.id,
                      uca_project_number=f"CLM{suffix}")
    db.add(project)
    db.flush()
    quote = Quote(project_id=project.id, quote_sequence=sequence, current_version=current_version,
                  markup_control_enabled=False, created_at=datetime(2026, 1, 1, 12, 0, 0))
    db.add(quote)
    db.flush()
    line = QuoteLineItem(quote_id=quote.id, item_type="part", description="[TEST] widget",
                         quantity=2, base_cost=100.0, markup_percent=25.0, unit_price=125.0,
                         original_markup_percent=25.0, qty_pending=2, qty_fulfilled=0)
    db.add(line)
    db.commit()
    return customer, project, quote, line


def _cleanup(db, *objs):
    for obj in objs:
        if obj is not None:
            db.delete(obj)
    db.commit()


def test_clone_carries_per_line_markup(db):
    """The reported bug: a cloned line keeps markup_percent (#230)."""
    customer, project, quote, _line = _make_quote_with_markup_line(db)
    quote_id = quote.id

    r = client.post(f"/quotes/{quote_id}/clone")
    assert r.status_code == 200, r.text
    clone_id = r.json()["id"]
    assert clone_id != quote_id

    db.expire_all()
    clone_lines = db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == clone_id).all()
    assert len(clone_lines) == 1
    assert clone_lines[0].markup_percent == 25.0            # carried over, not dropped
    assert clone_lines[0].base_cost == 100.0

    clone_quote = db.query(Quote).filter(Quote.id == clone_id).first()
    src_quote = db.query(Quote).filter(Quote.id == quote_id).first()
    _cleanup(db, clone_quote, src_quote, project, customer)


def test_revert_restores_per_line_markup(db):
    """Adjacent path: reverting to a snapshot version restores markup_percent (#230)."""
    # current_version must be AHEAD of the version we revert to (revert rejects
    # reverting to the current/future version).
    customer, project, quote, line = _make_quote_with_markup_line(db, current_version=2)
    quote_id = quote.id

    # A snapshot at version 1 capturing the line's markup (as create_snapshot would).
    snap = QuoteSnapshot(quote_id=quote_id, version=1, action_type="edit")
    db.add(snap)
    db.flush()
    db.add(QuoteLineItemSnapshot(
        snapshot_id=snap.id, original_line_item_id=line.id, item_type="part",
        description="[TEST] widget", quantity=2, unit_price=125.0, qty_pending=2,
        qty_fulfilled=0, is_deleted=False, original_markup_percent=25.0,
        markup_percent=25.0, base_cost=100.0,
    ))
    # Mutate the live line so revert has something to restore over.
    line.markup_percent = 99.0
    db.commit()

    r = client.post(f"/quotes/{quote_id}/revert/1")
    assert r.status_code == 200, r.text

    db.expire_all()
    lines = db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == quote_id).all()
    assert len(lines) == 1
    assert lines[0].markup_percent == 25.0                  # restored from the snapshot, not dropped

    refreshed = db.query(Quote).filter(Quote.id == quote_id).first()
    _cleanup(db, refreshed, project, customer)
