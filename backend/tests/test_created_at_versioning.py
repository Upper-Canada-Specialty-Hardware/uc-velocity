"""Tests for issue #148: editing the "created on" date of quotes, POs, and invoices.

Covers:
- created_at actually changes through the endpoint
- the entity version increments by exactly 1 per date edit
- a snapshot row is written with the acting user populated (via a monkeypatched actor)
- a frozen (invoiced) quote rejects a date edit
- the invoice snapshot/revert round-trip restores created_at
- the timezone round-trip: a UTC ISO string with offset is stored as naive-UTC unchanged
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import auth
import main
from database import SessionLocal
from models import (
    Profile, ProfileType, Project, Quote, QuoteLineItem,
    PurchaseOrder, POLineItem, POStatus, Invoice, InvoiceLineItem,
    QuoteSnapshot, POSnapshot, InvoiceSnapshot,
)
from utils import to_naive_utc

client = TestClient(main.app)


# A counter to keep UCA project numbers unique across this module's fixtures so we
# never collide with seeded data or other tests.
def _unique_suffix():
    return datetime.utcnow().strftime("%H%M%S%f")


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_project(db, suffix):
    customer = Profile(
        name=f"[TEST] Cust {suffix}",
        type=ProfileType.customer,
        pst="PST-TEST",
        address="123 Test St",
        postal_code="A1A1A1",
    )
    db.add(customer)
    db.flush()
    project = Project(
        name=f"[TEST] Project {suffix}",
        customer_id=customer.id,
        uca_project_number=f"TST{suffix}",
    )
    db.add(project)
    db.flush()
    return customer, project


def _cleanup(db, *objs):
    """Delete test objects, first removing snapshot rows whose FK back to the parent is
    NOT NULL with no DB/ORM cascade (quote_snapshots, po_snapshots). Without this, deleting
    a quote/PO that owns a date_edit snapshot raises an IntegrityError as SQLAlchemy tries
    to NULL the snapshot's FK. invoice_snapshots cascade via the ORM relationship, so the
    invoice itself can just be deleted."""
    for obj in objs:
        if isinstance(obj, Quote):
            snaps = db.query(QuoteSnapshot).filter(QuoteSnapshot.quote_id == obj.id).all()
        elif isinstance(obj, PurchaseOrder):
            snaps = db.query(POSnapshot).filter(POSnapshot.purchase_order_id == obj.id).all()
        else:
            snaps = []
        # ORM delete so cascade clears the *_line_item_snapshots children too.
        for snap in snaps:
            db.delete(snap)
    db.flush()
    for obj in objs:
        if obj is not None:
            db.delete(obj)
    db.commit()


# ----------------------------------------------------------------------------
# Quote date edit
# ----------------------------------------------------------------------------

def test_quote_created_at_changes_and_bumps_version_once(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    quote = Quote(project_id=project.id, quote_sequence=1, current_version=0,
                  created_at=datetime(2026, 1, 1, 12, 0, 0))
    db.add(quote)
    db.commit()
    quote_id = quote.id
    start_version = quote.current_version

    new_dt = "2026-03-15T09:30:00Z"
    r = client.put(f"/quotes/{quote_id}/created-at", json={"created_at": new_dt})
    assert r.status_code == 200, r.text
    body = r.json()

    # Version bumped by exactly 1
    assert body["current_version"] == start_version + 1

    # created_at changed (stored naive-UTC -> 09:30:00 with no offset)
    db.expire_all()
    refreshed = db.query(Quote).filter(Quote.id == quote_id).first()
    assert refreshed.created_at == datetime(2026, 3, 15, 9, 30, 0)

    # A date_edit snapshot row exists at the new version
    snap = (
        db.query(QuoteSnapshot)
        .filter(QuoteSnapshot.quote_id == quote_id, QuoteSnapshot.action_type == "date_edit")
        .first()
    )
    assert snap is not None
    assert snap.version == start_version + 1

    _cleanup(db, refreshed, project, customer)


def test_frozen_quote_rejects_date_edit(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    quote = Quote(project_id=project.id, quote_sequence=1, current_version=0,
                  created_at=datetime(2026, 1, 1, 12, 0, 0))
    db.add(quote)
    db.flush()
    # A fulfilled line item freezes the quote
    line = QuoteLineItem(quote_id=quote.id, item_type="misc", description="frozen",
                         quantity=1, unit_price=10.0, qty_pending=0, qty_fulfilled=1)
    db.add(line)
    db.commit()
    quote_id = quote.id

    r = client.put(f"/quotes/{quote_id}/created-at", json={"created_at": "2026-04-01T00:00:00Z"})
    assert r.status_code == 400
    assert "frozen" in r.json()["detail"].lower() or "invoiced" in r.json()["detail"].lower()

    # Version unchanged
    db.expire_all()
    refreshed = db.query(Quote).filter(Quote.id == quote_id).first()
    assert refreshed.current_version == 0

    _cleanup(db, refreshed, project, customer)


# ----------------------------------------------------------------------------
# PO date edit
# ----------------------------------------------------------------------------

def test_po_created_at_changes_and_bumps_version_once(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    vendor = Profile(name=f"[TEST] Vendor {suffix}", type=ProfileType.vendor,
                     pst="PST-V", address="1 Vendor Rd", postal_code="B2B2B2")
    db.add(vendor)
    db.flush()
    po = PurchaseOrder(project_id=project.id, vendor_id=vendor.id, po_sequence=1,
                       current_version=0, status=POStatus.draft,
                       created_at=datetime(2026, 2, 1, 8, 0, 0))
    db.add(po)
    db.commit()
    po_id = po.id

    r = client.put(f"/purchase-orders/{po_id}/created-at", json={"created_at": "2026-05-20T14:45:00Z"})
    assert r.status_code == 200, r.text
    assert r.json()["current_version"] == 1

    db.expire_all()
    refreshed = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    assert refreshed.created_at == datetime(2026, 5, 20, 14, 45, 0)

    snap = (
        db.query(POSnapshot)
        .filter(POSnapshot.purchase_order_id == po_id, POSnapshot.action_type == "date_edit")
        .first()
    )
    assert snap is not None and snap.version == 1

    _cleanup(db, refreshed, project, customer, vendor)


# ----------------------------------------------------------------------------
# Invoice date edit + snapshot/revert round-trip
# ----------------------------------------------------------------------------

def _make_invoice(db, suffix):
    customer, project = _make_project(db, suffix)
    quote = Quote(project_id=project.id, quote_sequence=1, current_version=0)
    db.add(quote)
    db.flush()
    invoice = Invoice(quote_id=quote.id, invoice_sequence=1, quote_version=0,
                      current_version=0, status="Sent",
                      created_at=datetime(2026, 1, 10, 10, 0, 0))
    db.add(invoice)
    db.flush()
    li = InvoiceLineItem(invoice_id=invoice.id, quote_line_item_id=None, item_type="misc",
                         description="svc", unit_price=100.0, qty_ordered=2,
                         qty_fulfilled_this_invoice=2, qty_fulfilled_total=2, qty_pending_after=0)
    db.add(li)
    db.commit()
    return customer, project, quote, invoice


def test_invoice_created_at_changes_and_bumps_version_once(db):
    suffix = _unique_suffix()
    customer, project, quote, invoice = _make_invoice(db, suffix)
    invoice_id = invoice.id

    r = client.put(f"/invoices/{invoice_id}/created-at", json={"created_at": "2026-06-01T16:00:00Z"})
    assert r.status_code == 200, r.text
    assert r.json()["current_version"] == 1

    db.expire_all()
    refreshed = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    assert refreshed.created_at == datetime(2026, 6, 1, 16, 0, 0)

    snap = (
        db.query(InvoiceSnapshot)
        .filter(InvoiceSnapshot.invoice_id == invoice_id, InvoiceSnapshot.action_type == "date_edit")
        .first()
    )
    assert snap is not None and snap.version == 1
    # entity_created_at is captured AT snapshot time, which is AFTER the endpoint set the
    # new created_at - so v1's entity_created_at is the new value, not the pre-edit one.
    assert snap.entity_created_at == datetime(2026, 6, 1, 16, 0, 0)

    refreshed = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    _cleanup(db, refreshed, quote, project, customer)


def test_invoice_revert_restores_created_at(db):
    suffix = _unique_suffix()
    customer, project, quote, invoice = _make_invoice(db, suffix)
    invoice_id = invoice.id

    # First edit -> version 1; its entity_created_at captures the value just set: 2026-02-02 02:00.
    client.put(f"/invoices/{invoice_id}/created-at", json={"created_at": "2026-02-02T02:00:00Z"})
    # Second edit -> version 2; its entity_created_at captures 2026-09-09 20:00.
    client.put(f"/invoices/{invoice_id}/created-at", json={"created_at": "2026-09-09T20:00:00Z"})

    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    assert inv.current_version == 2
    assert inv.created_at == datetime(2026, 9, 9, 20, 0, 0)

    # Reverting to version 1 restores the created_at captured in that snapshot
    # (the invoice's date at the moment version 1 was taken: 2026-02-02 02:00).
    r = client.post(f"/invoices/{invoice_id}/revert/1")
    assert r.status_code == 200, r.text

    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    # Revert restores the date from version 1's entity_created_at and bumps to v3.
    assert inv.created_at == datetime(2026, 2, 2, 2, 0, 0)
    assert inv.current_version == 3

    inv = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    _cleanup(db, inv, quote, project, customer)


# ----------------------------------------------------------------------------
# Actor stamping (monkeypatch the JWT verification to yield a fake actor)
# ----------------------------------------------------------------------------

def test_date_edit_snapshot_records_actor(db, monkeypatch):
    monkeypatch.setattr(
        auth, "extract_actor",
        lambda authorization: {"user_id": "user_test_123", "email": "tester@ucsh.com"},
    )
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    quote = Quote(project_id=project.id, quote_sequence=1, current_version=0,
                  created_at=datetime(2026, 1, 1, 12, 0, 0))
    db.add(quote)
    db.commit()
    quote_id = quote.id

    r = client.put(
        f"/quotes/{quote_id}/created-at",
        json={"created_at": "2026-03-15T09:30:00Z"},
        headers={"Authorization": "Bearer fake-token"},
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    snap = (
        db.query(QuoteSnapshot)
        .filter(QuoteSnapshot.quote_id == quote_id, QuoteSnapshot.action_type == "date_edit")
        .first()
    )
    assert snap is not None
    assert snap.actor_user_id == "user_test_123"
    assert snap.actor_email == "tester@ucsh.com"

    refreshed = db.query(Quote).filter(Quote.id == quote_id).first()
    _cleanup(db, refreshed, project, customer)


# ----------------------------------------------------------------------------
# Timezone round-trip unit test (no DB)
# ----------------------------------------------------------------------------

def test_to_naive_utc_offset_is_normalized_not_shifted():
    from datetime import timedelta

    # 09:30 at a -05:00 offset is 14:30 UTC; stored value must be the UTC wall clock.
    minus5 = datetime(2026, 3, 15, 9, 30, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert to_naive_utc(minus5) == datetime(2026, 3, 15, 14, 30, 0)
    assert to_naive_utc(minus5).tzinfo is None

    # A UTC ('Z') datetime keeps the same wall clock, just drops tzinfo.
    utc = datetime(2026, 3, 15, 9, 30, 0, tzinfo=timezone.utc)
    assert to_naive_utc(utc) == datetime(2026, 3, 15, 9, 30, 0)

    # A naive datetime is assumed already-UTC and returned unchanged.
    naive = datetime(2026, 3, 15, 9, 30, 0)
    assert to_naive_utc(naive) == naive
