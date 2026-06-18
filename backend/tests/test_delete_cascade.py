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
    Profile, ProfileType, Project, Quote, QuoteLineItem,
    PurchaseOrder, POStatus, POLineItem, POReceiving, POReceivingLineItem,
    Invoice, InvoiceLineItem, InvoiceSnapshot, InvoiceLineItemSnapshot,
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


# ---------------------------------------------------------------------------
# Issue #160: invoiced-quote and received-PO deletion (sibling defects of #158).
# Quote.invoices and PurchaseOrder.receivings also lacked cascade. The PO side
# additionally needs the new POSnapshot.receiving relationship so the unit of work
# deletes po_snapshots before the po_receivings their receiving_id points at.
# ---------------------------------------------------------------------------

def _add_invoiced_quote(db, project):
    """A quote with a line item, an invoice (+ line item + its own audit snapshot), and an
    'invoice' quote-snapshot. Exercises Quote.invoices cascade plus the invoice's own
    line_items / snapshots chains. invoice_snapshots.invoice_id is a real NOT NULL FK, so the
    invoice's snapshot rows must cascade-delete before the invoice itself."""
    quote = Quote(project_id=project.id, quote_sequence=1, current_version=1)
    db.add(quote)
    db.flush()
    db.add(QuoteLineItem(quote_id=quote.id, item_type="misc", description="svc",
                         quantity=1, unit_price=100.0, qty_pending=0, qty_fulfilled=1))
    invoice = Invoice(quote_id=quote.id, invoice_sequence=1, quote_version=1,
                      current_version=1, status="Sent")
    db.add(invoice)
    db.flush()
    db.add(InvoiceLineItem(invoice_id=invoice.id, quote_line_item_id=None, item_type="misc",
                           description="svc", unit_price=100.0, qty_ordered=1,
                           qty_fulfilled_this_invoice=1, qty_fulfilled_total=1, qty_pending_after=0))
    isnap = InvoiceSnapshot(invoice_id=invoice.id, version=1, action_type="date_edit")
    db.add(isnap)
    db.flush()
    db.add(InvoiceLineItemSnapshot(snapshot_id=isnap.id, item_type="misc", description="svc"))
    # quote snapshot recording the invoice action; invoice_id here is a bare int (no FK).
    qsnap = QuoteSnapshot(quote_id=quote.id, version=1, action_type="invoice", invoice_id=invoice.id)
    db.add(qsnap)
    db.flush()
    db.add(QuoteLineItemSnapshot(snapshot_id=qsnap.id, item_type="misc", description="svc",
                                 quantity=1, unit_price=100.0))
    db.flush()
    return quote, invoice, qsnap, isnap


def _add_received_po(db, project, vendor):
    """A PO with a line item, a receiving whose receiving-line-item references the PO line item,
    the v0 'create' snapshot, and a 'receive' snapshot whose receiving_id points at the receiving.
    Exercises BOTH delete-ordering dependencies on PO delete: po_snapshots -> po_receivings (the
    new relationship) and po_receiving_line_items -> po_line_items (pre-existing relationship)."""
    po = PurchaseOrder(project_id=project.id, vendor_id=vendor.id, po_sequence=1,
                       current_version=1, status=POStatus.received)
    db.add(po)
    db.flush()
    li = POLineItem(purchase_order_id=po.id, item_type="part", description="widget",
                    quantity=10, unit_price=5.0, qty_pending=0, qty_received=10)
    db.add(li)
    db.flush()
    receiving = POReceiving(purchase_order_id=po.id, received_date=datetime(2026, 6, 1, 12, 0, 0),
                            notes="[TEST] receipt")
    db.add(receiving)
    db.flush()
    db.add(POReceivingLineItem(receiving_id=receiving.id, po_line_item_id=li.id, item_type="part",
                               description="widget", unit_price=5.0, actual_unit_price=5.0,
                               qty_ordered=10, qty_received_this_receiving=10,
                               qty_received_total=10, qty_pending_after=0))
    create_snap = POSnapshot(purchase_order_id=po.id, version=0, action_type="create")
    db.add(create_snap)
    db.flush()
    db.add(POLineItemSnapshot(snapshot_id=create_snap.id, item_type="part", quantity=10, unit_price=5.0))
    recv_snap = POSnapshot(purchase_order_id=po.id, version=1, action_type="receive",
                           receiving_id=receiving.id)
    db.add(recv_snap)
    db.flush()
    db.add(POLineItemSnapshot(snapshot_id=recv_snap.id, item_type="part", quantity=10,
                              unit_price=5.0, qty_received=10))
    db.flush()
    return po, receiving, create_snap, recv_snap


def test_delete_invoiced_quote_returns_200_and_cascades(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    quote, invoice, qsnap, isnap = _add_invoiced_quote(db, project)
    db.commit()
    quote_id, invoice_id, isnap_id = quote.id, invoice.id, isnap.id

    r = client.delete(f"/quotes/{quote_id}")
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(Quote).filter(Quote.id == quote_id).first() is None
    assert db.query(Invoice).filter(Invoice.id == invoice_id).first() is None
    assert db.query(InvoiceLineItem).filter(InvoiceLineItem.invoice_id == invoice_id).count() == 0
    assert db.query(InvoiceSnapshot).filter(InvoiceSnapshot.id == isnap_id).first() is None
    assert (
        db.query(InvoiceLineItemSnapshot)
        .filter(InvoiceLineItemSnapshot.snapshot_id == isnap_id)
        .count()
    ) == 0

    db.delete(project)
    db.delete(customer)
    db.commit()


def test_delete_received_po_returns_200_and_cascades(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    vendor = _make_vendor(db, suffix)
    po, receiving, create_snap, recv_snap = _add_received_po(db, project, vendor)
    db.commit()
    po_id, receiving_id = po.id, receiving.id

    r = client.delete(f"/purchase-orders/{po_id}")
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() is None
    assert db.query(POReceiving).filter(POReceiving.id == receiving_id).first() is None
    assert (
        db.query(POReceivingLineItem)
        .filter(POReceivingLineItem.receiving_id == receiving_id)
        .count()
    ) == 0
    assert db.query(POSnapshot).filter(POSnapshot.purchase_order_id == po_id).count() == 0
    assert db.query(POLineItem).filter(POLineItem.purchase_order_id == po_id).count() == 0

    db.delete(project)
    db.delete(vendor)
    db.delete(customer)
    db.commit()


def test_delete_project_with_invoiced_quote_and_received_po_cascades(db):
    suffix = _unique_suffix()
    customer, project = _make_project(db, suffix)
    vendor = _make_vendor(db, suffix)
    quote, invoice, qsnap, isnap = _add_invoiced_quote(db, project)
    po, receiving, create_snap, recv_snap = _add_received_po(db, project, vendor)
    db.commit()
    project_id = project.id
    quote_id, invoice_id, po_id, receiving_id = quote.id, invoice.id, po.id, receiving.id

    r = client.delete(f"/projects/{project_id}")
    assert r.status_code == 200, r.text

    db.expire_all()
    assert db.query(Project).filter(Project.id == project_id).first() is None
    assert db.query(Quote).filter(Quote.id == quote_id).first() is None
    assert db.query(Invoice).filter(Invoice.id == invoice_id).first() is None
    assert db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first() is None
    assert db.query(POReceiving).filter(POReceiving.id == receiving_id).first() is None

    db.delete(vendor)
    db.delete(customer)
    db.commit()
