"""Tests for issue #158: deleting a quote/PO (or a project containing one) that owns
snapshot rows must cascade-delete the snapshots instead of trying to NULL their NOT NULL
parent FK (which raised psycopg2.errors.NotNullViolation -> 500, surfaced as "Failed to
fetch" once the Railway edge stripped CORS off the 5xx).

Each test builds the parent with at least one snapshot AND a line-item-snapshot child, so
it exercises the full cascade chain: parent -> *_snapshots -> *_line_item_snapshots. The
deletes go through the real endpoints, the same path the frontend and e2e cleanup hit.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
from database import SessionLocal
from models import (
    Profile, ProfileType, Project, Quote,
    PurchaseOrder, POStatus,
    QuoteSnapshot, QuoteLineItemSnapshot,
    POSnapshot, POLineItemSnapshot,
)

client = TestClient(main.app)


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
        name=f"[TEST] Cust {suffix}", type=ProfileType.customer,
        pst="PST-TEST", address="123 Test St", postal_code="A1A1A1",
    )
    db.add(customer)
    db.flush()
    project = Project(
        name=f"[TEST] Project {suffix}", customer_id=customer.id,
        uca_project_number=f"TST{suffix}",
    )
    db.add(project)
    db.flush()
    return customer, project


def _make_vendor(db, suffix):
    vendor = Profile(
        name=f"[TEST] Vendor {suffix}", type=ProfileType.vendor,
        pst="PST-V", address="1 Vendor Rd", postal_code="B2B2B2",
    )
    db.add(vendor)
    db.flush()
    return vendor


def _add_quote_with_snapshot(db, project):
    """A quote carrying one snapshot that itself owns a line-item-snapshot child."""
    quote = Quote(project_id=project.id, quote_sequence=1, current_version=1)
    db.add(quote)
    db.flush()
    snap = QuoteSnapshot(quote_id=quote.id, version=1, action_type="edit",
                         action_description="[TEST] cascade")
    db.add(snap)
    db.flush()
    db.add(QuoteLineItemSnapshot(snapshot_id=snap.id, item_type="misc",
                                 description="li", quantity=1, unit_price=1.0))
    db.flush()
    return quote, snap


def _add_po_with_snapshot(db, project, vendor):
    """A PO carrying the v0 'create' snapshot (every PO gets one) with a line-item child."""
    po = PurchaseOrder(project_id=project.id, vendor_id=vendor.id, po_sequence=1,
                       current_version=0, status=POStatus.draft)
    db.add(po)
    db.flush()
    snap = POSnapshot(purchase_order_id=po.id, version=0, action_type="create",
                      action_description="[TEST] cascade")
    db.add(snap)
    db.flush()
    db.add(POLineItemSnapshot(snapshot_id=snap.id, item_type="part",
                              quantity=1, unit_price=1.0))
    db.flush()
    return po, snap


def test_delete_snapshotted_quote_returns_200_and_cascades(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    quote, snap = _add_quote_with_snapshot(db, project)
    db.commit()
    quote_id, snap_id = quote.id, snap.id

    r = client.delete(f"/quotes/{quote_id}")
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(Quote).filter(Quote.id == quote_id).first() is None
    assert db.query(QuoteSnapshot).filter(QuoteSnapshot.id == snap_id).first() is None
    assert (
        db.query(QuoteLineItemSnapshot)
        .filter(QuoteLineItemSnapshot.snapshot_id == snap_id)
        .count()
    ) == 0

    db.delete(project)
    db.delete(customer)
    db.commit()


def test_delete_snapshotted_po_returns_200_and_cascades(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    vendor = _make_vendor(db, suffix)
    po, snap = _add_po_with_snapshot(db, project, vendor)
    db.commit()
    po_id, snap_id = po.id, snap.id

    r = client.delete(f"/purchase-orders/{po_id}")
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() is None
    assert db.query(POSnapshot).filter(POSnapshot.id == snap_id).first() is None
    assert (
        db.query(POLineItemSnapshot)
        .filter(POLineItemSnapshot.snapshot_id == snap_id)
        .count()
    ) == 0

    db.delete(project)
    db.delete(vendor)
    db.delete(customer)
    db.commit()


def test_delete_project_with_snapshotted_quote_and_po_cascades(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    vendor = _make_vendor(db, suffix)
    quote, qsnap = _add_quote_with_snapshot(db, project)
    po, posnap = _add_po_with_snapshot(db, project, vendor)
    db.commit()
    project_id, quote_id, po_id = project.id, quote.id, po.id
    qsnap_id, posnap_id = qsnap.id, posnap.id

    r = client.delete(f"/projects/{project_id}")
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(Project).filter(Project.id == project_id).first() is None
    assert db.query(Quote).filter(Quote.id == quote_id).first() is None
    assert db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() is None
    assert db.query(QuoteSnapshot).filter(QuoteSnapshot.id == qsnap_id).first() is None
    assert db.query(POSnapshot).filter(POSnapshot.id == posnap_id).first() is None
    assert (
        db.query(QuoteLineItemSnapshot)
        .filter(QuoteLineItemSnapshot.snapshot_id == qsnap_id)
        .count()
    ) == 0
    assert (
        db.query(POLineItemSnapshot)
        .filter(POLineItemSnapshot.snapshot_id == posnap_id)
        .count()
    ) == 0

    db.delete(vendor)
    db.delete(customer)
    db.commit()
