"""Tests for issue #283: a PO part line keeps and edits its own description.

A part line stores its description on the line: the inventory text by default,
or what purchasing typed for that one PO. Inventory is never written, a blank
edit resets the line to the inventory text, and version history and revert
carry the text like any other line field.

DB-dependent: skipped (via conftest) when Postgres is unreachable, like the other
DB tests; CI runs it against its Postgres.
"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
from database import SessionLocal
from models import (
    Part, Profile, ProfileType, Project, PurchaseOrder, POLineItem, POStatus, POSnapshot,
)

client = TestClient(main.app)

INVENTORY_TEXT = "[TEST] 41in rh push"  # the part's inventory description


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


def _make_draft_po(db):
    """Create a customer, project, vendor, part and an empty Draft PO.

    Args:
        db: Session to write with.

    Returns:
        ``(customer, project, vendor, part, po)`` all committed.
    """
    suffix = _suffix()
    customer = Profile(name=f"[TEST] Cust {suffix}", type=ProfileType.customer,
                       pst="PST-TEST", address="1 Test St", postal_code="A1A1A1")
    vendor = Profile(name=f"[TEST] Vendor {suffix}", type=ProfileType.vendor,
                     pst="PST-V", address="1 Vendor Rd", postal_code="B2B2B2")
    db.add_all([customer, vendor])
    db.flush()
    project = Project(name=f"[TEST] Proj {suffix}", customer_id=customer.id,
                      uca_project_number=f"POD{suffix}")
    part = Part(part_number=f"[TEST]-{suffix}", description=INVENTORY_TEXT, cost=10.0)
    db.add_all([project, part])
    db.flush()
    po = PurchaseOrder(project_id=project.id, vendor_id=vendor.id, po_sequence=1,
                       current_version=0, status=POStatus.draft)
    db.add(po)
    db.flush()
    # Every PO carries a version-0 "create" snapshot; commits bump from it.
    db.add(POSnapshot(purchase_order_id=po.id, version=0, action_type="create",
                      action_description="[TEST] create"))
    db.commit()
    return customer, project, vendor, part, po


def _cleanup(db, *objs):
    """Delete test rows (PO cascades to its lines and snapshots)."""
    db.expire_all()  # drop stale state left by the endpoint's own session
    for obj in objs:
        if obj is not None:
            db.delete(db.merge(obj))
    db.commit()


def _commit(po_id, *changes):
    """POST staged changes through the commit endpoint and return the response."""
    return client.post(f"/purchase-orders/{po_id}/commit", json={"changes": list(changes)})


def _lines(db, po_id):
    """Fresh read of a PO's lines after the endpoint's own session committed."""
    db.expire_all()
    return db.query(POLineItem).filter(POLineItem.purchase_order_id == po_id).all()


def _add_part(part, description=None):
    """A staged 'add' change for one unit of ``part``, optionally with text."""
    change = {"action": "add", "item_type": "part", "part_id": part.id,
              "quantity": 1, "unit_price": 10.0}
    if description is not None:
        change["description"] = description
    return change


def test_added_part_line_stores_inventory_text(db):
    """A part line added with no text keeps a copy of the inventory description."""
    customer, project, vendor, part, po = _make_draft_po(db)
    try:
        r = _commit(po.id, _add_part(part))
        assert r.status_code == 200, r.text
        [line] = _lines(db, po.id)
        assert line.description == INVENTORY_TEXT
    finally:
        _cleanup(db, po, part, project, vendor, customer)


def test_added_part_line_keeps_typed_text(db):
    """Text sent with a new part line is stored trimmed; inventory is untouched."""
    customer, project, vendor, part, po = _make_draft_po(db)
    try:
        r = _commit(po.id, _add_part(part, '  8200 41" rh push anodized clear '))
        assert r.status_code == 200, r.text
        [line] = _lines(db, po.id)
        assert line.description == '8200 41" rh push anodized clear'
        db.refresh(part)
        assert part.description == INVENTORY_TEXT
    finally:
        _cleanup(db, po, part, project, vendor, customer)


def test_edit_part_line_description_and_reset(db):
    """A part line's text can be edited, and a blank edit resets it to inventory."""
    customer, project, vendor, part, po = _make_draft_po(db)
    try:
        assert _commit(po.id, _add_part(part)).status_code == 200
        [line] = _lines(db, po.id)

        r = _commit(po.id, {"action": "edit", "line_item_id": line.id,
                            "description": "[TEST] vendor wording"})
        assert r.status_code == 200, r.text
        [line] = _lines(db, po.id)
        assert line.description == "[TEST] vendor wording"
        assert line.quantity == 1 and line.unit_price == 10.0  # untouched
        db.refresh(part)
        assert part.description == INVENTORY_TEXT  # inventory never written

        r = _commit(po.id, {"action": "edit", "line_item_id": line.id, "description": "   "})
        assert r.status_code == 200, r.text
        [line] = _lines(db, po.id)
        assert line.description == INVENTORY_TEXT
    finally:
        _cleanup(db, po, part, project, vendor, customer)


def test_revert_restores_part_line_description(db):
    """Reverting to the version before an edit brings the earlier text back."""
    customer, project, vendor, part, po = _make_draft_po(db)
    try:
        assert _commit(po.id, _add_part(part)).status_code == 200  # version 1
        [line] = _lines(db, po.id)
        assert _commit(po.id, {"action": "edit", "line_item_id": line.id,
                               "description": "[TEST] vendor wording"}).status_code == 200  # version 2

        r = client.post(f"/purchase-orders/{po.id}/revert/1")
        assert r.status_code == 200, r.text
        [line] = _lines(db, po.id)
        assert line.description == INVENTORY_TEXT
    finally:
        _cleanup(db, po, part, project, vendor, customer)


def test_single_line_endpoint_stores_inventory_text(db):
    """The one-line add endpoint follows the same rule as the batch commit."""
    customer, project, vendor, part, po = _make_draft_po(db)
    try:
        r = client.post(f"/purchase-orders/{po.id}/lines",
                        json={"item_type": "part", "part_id": part.id,
                              "quantity": 2, "unit_price": 10.0})
        assert r.status_code == 200, r.text
        assert r.json()["description"] == INVENTORY_TEXT
    finally:
        _cleanup(db, po, part, project, vendor, customer)


def _add_misc(description):
    """A staged 'add' change for one misc line with the given text."""
    return {"action": "add", "item_type": "misc", "description": description,
            "quantity": 1, "unit_price": 5.0}


def test_misc_line_text_is_trimmed_and_blank_is_refused(db):
    """Misc text is stored trimmed; blank or whitespace-only text is a 400 on add and edit."""
    customer, project, vendor, part, po = _make_draft_po(db)
    try:
        assert _commit(po.id, _add_misc("   ")).status_code == 400  # whitespace-only add refused
        assert _lines(db, po.id) == []                               # whole commit rolled back

        r = _commit(po.id, _add_misc("  [TEST] shipping  "))
        assert r.status_code == 200, r.text
        [line] = _lines(db, po.id)
        assert line.description == "[TEST] shipping"                 # stored trimmed

        for blank in ("", "   "):                                    # explicit blank edit refused
            r = _commit(po.id, {"action": "edit", "line_item_id": line.id, "description": blank})
            assert r.status_code == 400, r.text
        [line] = _lines(db, po.id)
        assert line.description == "[TEST] shipping"                 # text unchanged

        r = _commit(po.id, {"action": "edit", "line_item_id": line.id, "quantity": 3})
        assert r.status_code == 200, r.text                          # omitted text keeps the stored text
        [line] = _lines(db, po.id)
        assert line.description == "[TEST] shipping" and line.quantity == 3
    finally:
        _cleanup(db, po, part, project, vendor, customer)


def test_single_line_endpoints_refuse_blank_misc_text(db):
    """The one-line add and update endpoints refuse blank misc text too."""
    customer, project, vendor, part, po = _make_draft_po(db)
    try:
        body = {"item_type": "misc", "description": "  ", "quantity": 1, "unit_price": 5.0}
        assert client.post(f"/purchase-orders/{po.id}/lines", json=body).status_code == 400

        r = client.post(f"/purchase-orders/{po.id}/lines", json={**body, "description": " [TEST] crate "})
        assert r.status_code == 200, r.text
        assert r.json()["description"] == "[TEST] crate"
        line_id = r.json()["id"]
        r = client.put(f"/purchase-orders/{po.id}/lines/{line_id}", json=body)
        assert r.status_code == 400, r.text
    finally:
        _cleanup(db, po, part, project, vendor, customer)
