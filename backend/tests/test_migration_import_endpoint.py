"""End-to-end test of the legacy import endpoint's close-state (issue #164).

Uploads a minimal set of UC Vision CSV exports through ``POST /migration/import``
and checks what lands in the database: each quote's status comes from its
lines' real shipped quantities (or a force-close), purchase orders derive their
status from line receipts, and every row carries its Vision origin keys.

The endpoint wipes and reloads the catalogue / profile / project tables, so this
test must only ever run against a scratch or CI database (never production).
``conftest.py`` skips it when no local Postgres is up, and the module-level guard
below refuses to run against any DATABASE_URL that is not a local/loopback host,
so a stray production URL can never be truncated by this test.
"""
import os
from urllib.parse import urlparse

import pytest

# --- Safety guard: this test TRUNCATEs tables, so it must target only a local DB.
# Gate on the actual DATABASE_URL host, not merely on a port being reachable: a
# production URL in the environment must skip this test, never get wiped by it.
# The check runs BEFORE importing the app so a non-local URL never even opens a
# connection at collection time.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
_BLOCKED_MARKERS = ("rlwy.net", "railway.internal", "railway.app", "proxy.rlwy")


def _target_is_local() -> bool:
    """True only when DATABASE_URL points at a local, non-Railway host."""
    dsn = os.getenv("DATABASE_URL", "")
    if any(marker in dsn for marker in _BLOCKED_MARKERS):   # obvious managed host -> never
        return False
    try:
        host = (urlparse(dsn).hostname or "").lower()       # parsed host, or "" for a socket DSN
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


if not _target_is_local():
    # Skip the WHOLE module before importing the app -- no engine, no connection.
    pytest.skip(
        "destructive import test runs only against a local DATABASE_URL",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient

import main
from database import SessionLocal
from models import Quote, QuoteLineItem, PurchaseOrder, POLineItem, POStatus, Project, Profile, Category

client = TestClient(main.app)


def _csv(header: str, *rows: str) -> bytes:
    """Build a CSV file body from a header line and row lines."""
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


# One customer, one vendor, one project, and a small catalogue. The catalogue
# exercises the legacy stamping on categories/parts/labour/misc and the
# "Application & Material" split into two Category rows.
FIXTURES = {
    "tblPartsCategories.csv": _csv(
        "CategoryID,chrCategoryName,chrCategoryType",
        "1,Doors,Material",           # -> one part category
        "2,Install,Application",      # -> one labour category
        "3,Combined,Application & Material",  # -> BOTH a part and a labour category
    ),
    "tblClients.csv": _csv(
        "Client ID,chrCompanyName,chrAddress,chrCity,chrProvince,chrPostalCode,chrProvincialTax",
        "1,Test Customer,1 Main St,Toronto,ON,M1M1M1,PST-1",
    ),
    "tblVendors.csv": _csv(
        "VendorID,chrCompanyName,chrAddress,chrCity,chrProvince,chrPostalCode,chrProvincialTax",
        "1,Test Vendor,2 Side St,Toronto,ON,M2M2M2,PST-2",
    ),
    "tblMaterial.csv": _csv(
        "ProductID,chrProductName,chrProductDescription,curNetPrice,intMarkup,intVendor,intCategory",
        "1,DOOR-1,Steel door,100.00,50,1,1",
    ),
    "tblApplication.csv": _csv(
        "ProductID,chrProductDescription,intTime,curNetPrice,intMarkup,intCategory",
        "1,Hang door,2,90.00,50,2",
    ),
    "tblZones.csv": _csv(
        "ZoneRateID,chrZones,chrDistance,curNetPrice,intMarkup",
        "1,Zone A,0-50km,40.00,0",
    ),
    "tblProjects.csv": _csv(
        "ProjectID,ProjectName,ClientID,UCAProjectNr,dtmStartDate,blnArchive",
        "10,Test Project,1,9001,1/2/2020 0:00:00,0",
        "11,Archived Project,1,9002,1/2/2020 0:00:00,-1",  # Access TRUE = -1 -> archived
    ),
    # Six work orders, one per status path:
    #   101 partially shipped (total)       -> Invoiced (has PO)
    #   102 fully shipped                   -> Closed
    #   103 force-closed by text, partial   -> Closed (override)
    #   104 no ship data, no PO             -> Draft (fully pending, counted)
    #   105 nothing shipped + client PO     -> Work Order
    #   106 partially shipped via BACK-ORDER-> Invoiced (fallback path)
    "tblServiceRecords.csv": _csv(
        "WorkorderID,PojectID,dtmDateStarted,memWorkDescription,intPONumber,chrStatus,blnForceClosed",
        "101,10,1/3/2020 0:00:00,Partial job,555,Open,0",
        "102,10,1/4/2020 0:00:00,Finished job,,Closed,0",
        "103,10,1/5/2020 0:00:00,Abandoned job,,Force Closed by DP.,0",  # force-close by TEXT, no flag
        "104,10,1/6/2020 0:00:00,Unknown job,,,",
        "105,10,1/7/2020 0:00:00,Not started,556,Open,0",
        "106,10,1/8/2020 0:00:00,Back-ordered job,,Open,0",
    ),
    # Lines cover all three tables and both shipped signals. WO 101 labour uses the
    # running total; 106 part uses only a back-order; 104 misc has no ship data.
    "tblWorkorderApplication.csv": _csv(
        "WorkorderPartID,intWorkorderID,intProductName,chrProductDescription,intQuantity,"
        "curUnitPrice,curNetPrice,intTotalShippedQuantity,intShipQuantity,intQuantityBO",
        "1001,101,1,Install hinges,10,25.00,18.00,3,,",
        "1002,102,1,Install locks,4,30.00,20.00,4,,",
        "1003,103,1,Install closers,10,40.00,30.00,2,,",
        "1005,105,1,Install panic bars,5,60.00,45.00,0,,",
    ),
    "tblWorkorderMaterial.csv": _csv(
        "WorkorderPartID,intWorkorderID,intProductName,chrProductDescription,intQuantity,"
        "curUnitPrice,curNetPrice,intTotalShippedQuantity,intShipQuantity,intQuantityBO",
        # WO 106 part: only a back-order of 4 of 10 -> 6 shipped, 4 pending (fallback path).
        "2001,106,1,Door hardware,10,15.00,10.00,,,4",
        # WO 102 part: single-shipment field only, fully shipped.
        "2002,102,1,Locks pack,2,12.00,8.00,,2,",
    ),
    "tblWorkorderZones.csv": _csv(
        "WorkorderPartID,intWorkorderID,chrZones,chrDistance,intQuantity,"
        "curPrice,curNetPrice,intTotalShippedQuantity,intShipQuantity,intQuantityBO",
        # WO 104 misc: no ship data at all -> fully pending, counted.
        "3001,104,1,0-50km,5,50.00,40.00,,,",
    ),
    # Three POs: fully received, partially received, nothing received. The
    # third claims "received all" in Vision but its lines say otherwise.
    "tblPurchaseOrders.csv": _csv(
        "PurchaseOrderID,intProjectID,intVendorID,dtmOrderDate,memNote,blnRecievedAll",
        "201,10,1,2/1/2020 0:00:00,All here,-1",
        "202,10,1,2/2/2020 0:00:00,Some here,0",
        "203,10,1,2/3/2020 0:00:00,Nothing here,-1",
    ),
    "tblPurchaseOrdersMaterial.csv": _csv(
        "PurchaseOrderPartID,intPurchaseOrderID,intProductID,chrProductDescription,intQtyOrdered,"
        "curUnitPrice,intQtyReceived1,intQtyReceived2,intQtyReceived3",
        "301,201,,Hinges,4,5.00,4,0,0",
        "302,202,,Locks,10,6.00,3,0,0",
        "303,203,,Closers,2,7.00,0,0,0",
    ),
}


def _upload():
    """POST the fixture CSVs as a multipart upload and return the JSON result."""
    files = [("files", (name, body, "text/csv")) for name, body in FIXTURES.items()]
    r = client.post("/migration/import", files=files)
    assert r.status_code == 200, r.text
    return r.json()


def _quotes_by_wo(db) -> dict[int, Quote]:
    rows = db.query(Quote).filter(Quote.legacy_source == "tblServiceRecords").all()
    return {q.legacy_id: q for q in rows}


def test_import_derives_quote_status_from_vision_ship_data():
    result = _upload()
    assert result["success"] is True
    assert result["counts"]["quotes"] == 6
    # The operator sees the split up front: closed = 102, 103; open = 101, 104, 105, 106.
    assert result["counts"]["quotes_closed"] == 2
    assert result["counts"]["quotes_open"] == 4
    assert result["counts"]["quotes_force_closed"] == 1
    assert result["counts"]["quote_lines_without_ship_data"] == 1   # only WO 104's misc line
    assert any("no ship quantities" in w for w in result["warnings"])

    db = SessionLocal()
    try:
        quotes = _quotes_by_wo(db)
        assert set(quotes) == {101, 102, 103, 104, 105, 106}

        # Partially shipped (running total) with a PO -> Invoiced, 3 of 10 fulfilled.
        q101 = quotes[101]
        assert q101.status == "Invoiced"
        (li,) = q101.line_items
        assert (li.qty_fulfilled, li.qty_pending) == (3, 7)
        assert li.legacy_source == "tblWorkorderApplication" and li.legacy_id == 1001

        # Fully shipped across a labour line (total) and a part line (single ship) -> Closed.
        assert quotes[102].status == "Closed"
        by_type = {li.item_type: li for li in quotes[102].line_items}
        assert (by_type["labor"].qty_fulfilled, by_type["labor"].qty_pending) == (4, 0)
        assert (by_type["part"].qty_fulfilled, by_type["part"].qty_pending) == (2, 0)   # intShipQuantity path

        # Force-closed BY TEXT (no flag) overrides the partial shipment -> Closed, whole line done.
        assert quotes[103].status == "Closed"
        (li,) = quotes[103].line_items
        assert (li.qty_fulfilled, li.qty_pending) == (10, 0)

        # Misc line with no ship data, no PO -> Draft, nothing claimed done.
        assert quotes[104].status == "Draft"
        (li,) = quotes[104].line_items
        assert li.item_type == "misc"
        assert (li.qty_fulfilled, li.qty_pending) == (0, 5)

        # Nothing shipped (total = 0) but has a PO -> Work Order.
        assert quotes[105].status == "Work Order"
        (li,) = quotes[105].line_items
        assert (li.qty_fulfilled, li.qty_pending) == (0, 5)

        # Back-order-only part line: 4 of 10 back-ordered -> 6 shipped, 4 pending -> Invoiced.
        assert quotes[106].status == "Invoiced"
        (li,) = quotes[106].line_items
        assert li.item_type == "part"
        assert (li.qty_fulfilled, li.qty_pending) == (6, 4)
        assert li.legacy_source == "tblWorkorderMaterial" and li.legacy_id == 2001

        # Every migrated quote is flagged and keyed to its Vision origin.
        for q in quotes.values():
            assert q.legacy_imported is True
            assert q.work_description.startswith(f"[WO {q.legacy_id}]")
    finally:
        db.close()


def test_import_stamps_legacy_keys_on_catalogue():
    _upload()
    db = SessionLocal()
    try:
        # "Application & Material" (CategoryID 3) splits into a part AND a labour category,
        # both keyed to the same Vision id -- the composite legacy index keeps them distinct.
        combined = db.query(Category).filter(
            Category.legacy_source == "tblPartsCategories", Category.legacy_id == 3
        ).all()
        assert {c.type for c in combined} == {"part", "labor"}
        # Each catalogue table stamps its own source + id.
        from models import Part, Labor, Miscellaneous
        assert db.query(Part).filter(Part.legacy_source == "tblMaterial", Part.legacy_id == 1).count() == 1
        assert db.query(Labor).filter(Labor.legacy_source == "tblApplication", Labor.legacy_id == 1).count() == 1
        assert db.query(Miscellaneous).filter(
            Miscellaneous.legacy_source == "tblZones", Miscellaneous.legacy_id == 1
        ).count() == 1
    finally:
        db.close()


def test_import_derives_po_status_from_line_receipts():
    result = _upload()
    assert result["counts"]["purchase_orders"] == 3
    assert result["counts"]["purchase_orders_received"] == 1
    assert result["counts"]["purchase_orders_sent"] == 1
    assert result["counts"]["purchase_orders_draft"] == 1
    # PO 203 claims received-all but nothing was received: reported, receipts win.
    assert any("received-all flag disagrees" in w and "1 " in w for w in result["warnings"])

    db = SessionLocal()
    try:
        pos = {po.legacy_id: po for po in
               db.query(PurchaseOrder).filter(PurchaseOrder.legacy_source == "tblPurchaseOrders").all()}
        assert pos[201].status is POStatus.received
        assert pos[202].status is POStatus.sent
        assert pos[203].status is POStatus.draft
        for po in pos.values():
            assert po.legacy_imported is True
        (li,) = pos[202].line_items
        assert (li.qty_received, li.qty_pending) == (3, 7)
        assert li.legacy_source == "tblPurchaseOrdersMaterial" and li.legacy_id == 302
    finally:
        db.close()


def test_import_stamps_legacy_keys_on_profiles_and_projects():
    # Upload twice up front (the button pressed twice): the second run must yield
    # the same rows, not duplicates. Both uploads happen BEFORE any query session
    # is opened -- an open session holds table locks that the endpoint's TRUNCATE
    # would wait on forever.
    _upload()
    _upload()
    db = SessionLocal()
    try:
        customer = db.query(Profile).filter(Profile.legacy_source == "tblClients").one()
        assert customer.legacy_id == 1
        vendor = db.query(Profile).filter(Profile.legacy_source == "tblVendors").one()
        assert vendor.legacy_id == 1
        active = db.query(Project).filter(Project.legacy_id == 10).one()
        assert active.legacy_source == "tblProjects" and active.uca_project_number == "A9001"
        assert active.status == "active"
        # blnArchive = -1 (Access TRUE) must import as archived, not active.
        archived = db.query(Project).filter(Project.legacy_id == 11).one()
        assert archived.status == "archived"
        # Idempotent: a second press yields the same rows, not duplicates.
        assert db.query(Quote).filter(Quote.legacy_source == "tblServiceRecords").count() == 6
        assert db.query(QuoteLineItem).filter(QuoteLineItem.legacy_source.isnot(None)).count() == 7
        assert db.query(POLineItem).filter(POLineItem.legacy_source.isnot(None)).count() == 3
        # Legacy keys stayed unique across the two runs (no duplicate (source, id) pairs).
        pairs = db.query(QuoteLineItem.legacy_source, QuoteLineItem.legacy_id).filter(
            QuoteLineItem.legacy_id.isnot(None)
        ).all()
        assert len(pairs) == len(set(pairs))
    finally:
        db.close()
