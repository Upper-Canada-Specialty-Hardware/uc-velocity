"""Regression tests for issue #233: committing a partial edit to a PO line item.

The PO editor stages only the fields the user changed, so the commit request can
carry an edit with no ``quantity`` (price-only or description-only change) or no
``unit_price`` (quantity-only change). ``commit_po_edits`` used those values
directly: a missing quantity crashed the received-quantity comparison (an
unhandled 500 that the browser showed as "Failed to fetch"), and a missing price
was written to the line as NULL. Each field must fall back to the line's current
value when the edit omits it, and the received-quantity guard must still fire.

DB-dependent: skipped (via conftest) when Postgres is unreachable, like the other
DB tests; CI runs it against its Postgres.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
from database import SessionLocal
from models import (
    Profile, ProfileType, Project, PurchaseOrder, POLineItem, POStatus, POSnapshot,
)

client = TestClient(main.app)


@pytest.fixture
def db():
    """One SQLAlchemy session per test, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _suffix():
    """Unique tag so parallel/leftover test rows never collide on names."""
    return datetime.utcnow().strftime("%H%M%S%f")


def _make_draft_po(db, *, quantity=5, unit_price=84.0, qty_received=0):
    """Create a customer, project, vendor, and a Draft PO with one misc line.

    Args:
        db: Session to write with.
        quantity: Ordered quantity on the single line.
        unit_price: Unit price on the single line.
        qty_received: Already-received quantity (drives qty_pending and the guard).

    Returns:
        ``(customer, project, vendor, po, line)`` all flushed and committed.
    """
    suffix = _suffix()
    customer = Profile(name=f"[TEST] Cust {suffix}", type=ProfileType.customer,
                       pst="PST-TEST", address="1 Test St", postal_code="A1A1A1")
    vendor = Profile(name=f"[TEST] Vendor {suffix}", type=ProfileType.vendor,
                     pst="PST-V", address="1 Vendor Rd", postal_code="B2B2B2")
    db.add_all([customer, vendor])
    db.flush()
    project = Project(name=f"[TEST] Proj {suffix}", customer_id=customer.id,
                      uca_project_number=f"POE{suffix}")
    db.add(project)
    db.flush()
    po = PurchaseOrder(project_id=project.id, vendor_id=vendor.id, po_sequence=1,
                       current_version=0, status=POStatus.draft)
    db.add(po)
    db.flush()
    # Every PO carries a version-0 "create" snapshot; commit_po_edits bumps from it.
    db.add(POSnapshot(purchase_order_id=po.id, version=0, action_type="create",
                      action_description="[TEST] create"))
    line = POLineItem(purchase_order_id=po.id, item_type="misc",
                      description="[TEST] misc line", quantity=quantity,
                      unit_price=unit_price, qty_received=qty_received,
                      qty_pending=max(0, quantity - qty_received))
    db.add(line)
    db.commit()
    return customer, project, vendor, po, line


def _cleanup(db, *objs):
    """Delete test rows (PO cascades to its lines and snapshots)."""
    for obj in objs:
        if obj is not None:
            db.delete(obj)
    db.commit()


def _commit(po_id, change):
    """POST one staged edit through the commit endpoint and return the response."""
    return client.post(f"/purchase-orders/{po_id}/commit", json={"changes": [change]})


def _reload_line(db, line_id):
    """Fresh read of a line after the endpoint's own session committed."""
    db.expire_all()
    return db.query(POLineItem).filter(POLineItem.id == line_id).first()


def test_price_only_edit_keeps_quantity(db):
    """The reported bug: an edit carrying only unit_price must not 500 (#233)."""
    customer, project, vendor, po, line = _make_draft_po(db, quantity=5, unit_price=84.0)
    try:
        r = _commit(po.id, {"action": "edit", "line_item_id": line.id, "unit_price": 90.0})
        assert r.status_code == 200, r.text

        fresh = _reload_line(db, line.id)
        assert fresh.unit_price == 90.0
        assert fresh.quantity == 5            # untouched
        assert fresh.qty_pending == 5         # recomputed from the kept quantity
        assert r.json()["snapshot_version"] == 1
    finally:
        _cleanup(db, po, project, vendor, customer)


def test_quantity_only_edit_keeps_unit_price(db):
    """The silent half of #233: an edit carrying only quantity must not NULL the price."""
    customer, project, vendor, po, line = _make_draft_po(db, quantity=5, unit_price=84.0)
    try:
        r = _commit(po.id, {"action": "edit", "line_item_id": line.id, "quantity": 8})
        assert r.status_code == 200, r.text

        fresh = _reload_line(db, line.id)
        assert fresh.quantity == 8
        assert fresh.qty_pending == 8
        assert fresh.unit_price == 84.0       # untouched, not NULL
    finally:
        _cleanup(db, po, project, vendor, customer)


def test_description_only_edit_keeps_quantity_and_price(db):
    """A misc-line description change sends neither quantity nor price."""
    customer, project, vendor, po, line = _make_draft_po(db, quantity=5, unit_price=84.0)
    try:
        r = _commit(po.id, {"action": "edit", "line_item_id": line.id,
                            "description": "[TEST] renamed"})
        assert r.status_code == 200, r.text

        fresh = _reload_line(db, line.id)
        assert fresh.description == "[TEST] renamed"
        assert fresh.quantity == 5
        assert fresh.unit_price == 84.0
    finally:
        _cleanup(db, po, project, vendor, customer)


def test_quantity_cannot_drop_below_received(db):
    """The received-quantity guard still rejects a reduction below what was received."""
    customer, project, vendor, po, line = _make_draft_po(db, quantity=5, unit_price=84.0,
                                                         qty_received=3)
    try:
        r = _commit(po.id, {"action": "edit", "line_item_id": line.id, "quantity": 2})
        assert r.status_code == 400, r.text
        assert "already received" in r.json()["detail"]

        fresh = _reload_line(db, line.id)
        assert fresh.quantity == 5            # nothing written
        assert fresh.qty_pending == 2
    finally:
        _cleanup(db, po, project, vendor, customer)


def test_price_only_edit_on_partially_received_line(db):
    """A price change on a partially received line keeps its quantity and pending count."""
    customer, project, vendor, po, line = _make_draft_po(db, quantity=5, unit_price=84.0,
                                                         qty_received=3)
    try:
        r = _commit(po.id, {"action": "edit", "line_item_id": line.id, "unit_price": 99.5})
        assert r.status_code == 200, r.text

        fresh = _reload_line(db, line.id)
        assert fresh.unit_price == 99.5
        assert fresh.quantity == 5
        assert fresh.qty_received == 3
        assert fresh.qty_pending == 2
    finally:
        _cleanup(db, po, project, vendor, customer)
