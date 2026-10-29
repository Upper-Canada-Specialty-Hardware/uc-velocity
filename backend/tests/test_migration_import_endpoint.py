"""End-to-end test of the legacy import endpoint's close-state (issue #164).

Uploads a minimal set of UC Vision CSV exports through ``POST /migration/import``
and checks what lands in the database: a work order Vision finished (all shipped
or force-closed) lands Closed with every line done, any other quote's status
comes from its lines' real shipped quantities, quantities keep their fractions,
zeros and credits, purchase orders follow Vision's received-all flag or else
their line receipts, and every row carries its Vision origin keys.

The endpoint wipes and reloads the catalogue / profile / project tables, so this
test must only ever run against a scratch or CI database (never production).
``conftest.py`` skips it when no local Postgres is up, and the module-level guard
below refuses to run against any DATABASE_URL that is not a local/loopback host,
so a stray production URL can never be truncated by this test.
"""
import os
from datetime import datetime
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
from models import (
    Quote, QuoteLineItem, PurchaseOrder, POLineItem, POStatus, Project, Profile, ProfileType, Category,
)

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
        "2,Short call,0.083,6.81,50,2",          # 3-decimal hours: stored as 0.08, cost kept
    ),
    "tblZones.csv": _csv(
        "ZoneRateID,chrZones,chrDistance,curNetPrice,intMarkup",
        "1,Zone A,0-50km,40.00,0",
    ),
    "tblProjects.csv": _csv(
        "ProjectID,ProjectName,ClientID,UCAProjectNr,dtmStartDate,blnArchive",
        "10,Test Project,1,9001,1/2/2020 0:00:00,0",
        "11,Archived Project,1,9002,1/2/2020 0:00:00,-1",  # Access TRUE = -1 -> archived
        # Unknown customers: kept (not skipped) on ONE shared placeholder customer.
        "12,Orphan Project,99,9003,1/2/2020 0:00:00,0",     # ClientID 99 not in tblClients
        "13,Second Orphan,98,9004,1/2/2020 0:00:00,0",      # a different unknown id reuses the placeholder
    ),
    # Fifteen work orders, one per status path:
    #   101 partially shipped (total)       -> Invoiced (has PO)
    #   102 fully shipped, flag not set     -> Closed (by its lines)
    #   103 force-closed by text, partial   -> Closed (override)
    #   104 no ship data, no PO             -> Draft (fully pending, counted)
    #   105 nothing shipped + client PO     -> Work Order
    #   106 partially shipped via BACK-ORDER-> Invoiced (fallback path)
    #   107 blnAllShipped -1, lines say 2/10-> Closed (Vision's verdict beats the lines)
    #   108 blnAllShipped True, no lines    -> Closed (counted: nothing shipped on record)
    #   109 blnForceClosed flag, zero+credit-> Closed (counted: nothing shipped on record)
    #   110 open, all positive lines shipped plus a zero and two credit lines -> Closed
    #   111 open, fractional lines, 0.5 of 1.5 shipped -> Invoiced
    #   112 blank start date                -> created_at from dtmDateEntered (no lines: Draft)
    #   113 on a project with an unknown customer -> still imported (no lines: Draft)
    #   114 open, only a processed credit line -> Closed (nothing pending)
    #   115 open + PO, processed credit, positive line unshipped -> Invoiced (not Work Order)
    "tblServiceRecords.csv": _csv(
        "WorkorderID,PojectID,dtmDateStarted,memWorkDescription,intPONumber,chrStatus,blnForceClosed,"
        "blnAllShipped,dtmDateEntered",
        "101,10,1/3/2020 0:00:00,Partial job,555,Open,0,0,",
        "102,10,1/4/2020 0:00:00,Finished job,,Closed,0,0,",
        "103,10,1/5/2020 0:00:00,Abandoned job,,Force Closed by DP.,0,0,",  # force-close by TEXT, no flag
        "104,10,1/6/2020 0:00:00,Unknown job,,,,,",
        "105,10,1/7/2020 0:00:00,Not started,556,Open,0,0,",
        "106,10,1/8/2020 0:00:00,Back-ordered job,,Open,0,0,",
        "107,10,1/9/2020 0:00:00,Finished by flag,,Closed,0,-1,",          # all-shipped flag (Access -1)
        "108,10,1/10/2020 0:00:00,Finished empty job,,Closed,0,True,",     # all-shipped flag (True), no lines
        "109,10,1/11/2020 0:00:00,Credit only job,,Open,-1,0,",            # force-close by FLAG, status text silent
        "110,10,1/12/2020 0:00:00,Shipped with extras,,Open,0,0,",
        "111,10,1/13/2020 0:00:00,Fractional job,557,Open,0,0,",
        "112,10,,Progress billing,,Open,0,0,3/15/2020 0:00:00",            # no start date, entry date only
        "113,12,1/14/2020 0:00:00,Orphan job,,Open,0,0,",                  # project 12 has an unknown customer
        "114,10,1/15/2020 0:00:00,Credit memo,,Open,0,0,",
        "115,10,1/16/2020 0:00:00,Credit then more work,558,Open,0,0,",
    ),
    # Lines cover all three tables and both shipped signals. WO 101 labour uses the
    # running total; 106 part uses only a back-order; 104 misc has no ship data.
    # Line ids 1071.. / 2071.. / 3091.. are "work order + line number" for readability.
    "tblWorkorderApplication.csv": _csv(
        "WorkorderPartID,intWorkorderID,intProductName,chrProductDescription,intQuantity,"
        "curUnitPrice,curNetPrice,intTotalShippedQuantity,intShipQuantity,intQuantityBO",
        "1001,101,1,Install hinges,10,25.00,18.00,3,,",
        "1002,102,1,Install locks,4,30.00,20.00,4,,",
        "1003,103,1,Install closers,10,40.00,30.00,2,,",
        "1005,105,1,Install panic bars,5,60.00,45.00,0,,",
        "1071,107,1,Install frames,10,25.00,18.00,2,,",           # lines say 2 of 10; header says finished
        "1091,109,1,Survey only,0,25.00,18.00,,,",                # zero line (quoted, not ordered)
        "1101,110,1,Quoted extra,0,25.00,18.00,0,,",              # zero line on an open job
        "1102,110,1,Labour credit,-3,25.00,18.00,-3,,",           # credit Vision processed -> fulfilled
        "1111,111,1,Adjust closers,2.333,25.00,18.00,2.333,,",    # rounds to 2.33, fully shipped
        "1141,114,1,Labour refund,-2,25.00,18.00,-2,,",           # the job's only line: a processed credit
        "1151,115,1,Labour refund,-1,25.00,18.00,,-1,",           # processed credit (single-shipment field)
    ),
    "tblWorkorderMaterial.csv": _csv(
        "WorkorderPartID,intWorkorderID,intProductName,chrProductDescription,intQuantity,"
        "curUnitPrice,curNetPrice,intTotalShippedQuantity,intShipQuantity,intQuantityBO",
        # WO 106 part: only a back-order of 4 of 10 -> 6 shipped, 4 pending (fallback path).
        "2001,106,1,Door hardware,10,15.00,10.00,,,4",
        # WO 102 part: single-shipment field only, fully shipped.
        "2002,102,1,Locks pack,2,12.00,8.00,,2,",
        "2071,107,1,Frame kit,3,15.00,10.00,,,",                  # no ship data; finished header fulfils it
        "2101,110,1,Strike plates,4,15.00,10.00,,4,",             # the open job's only positive line, shipped
        "2151,115,1,Replacement locks,3,15.00,10.00,0,,",         # positive line, nothing shipped yet
    ),
    "tblWorkorderZones.csv": _csv(
        "WorkorderPartID,intWorkorderID,chrZones,chrDistance,intQuantity,"
        "curPrice,curNetPrice,intTotalShippedQuantity,intShipQuantity,intQuantityBO",
        # WO 104 misc: no ship data at all -> fully pending, counted.
        "3001,104,1,0-50km,5,50.00,40.00,,,",
        "3091,109,1,Credit km,-2,50.00,40.00,,,",                 # credit, no ship data, force-closed job
        "3101,110,1,Credit km,-1,50.00,40.00,,,",                 # credit never processed, open job
        "3111,111,1,0-50km,1.5,50.00,40.00,0.5,,",                # 0.5 of 1.5 shipped
    ),
    # Six POs, one per status path (blnRecievedAll is Vision's spelling):
    #   201 received-all, receipts match    -> Received
    #   202 flag off, 3 of 10 received      -> Sent (receipts summed over the 3 slots)
    #   203 received-all, nothing received  -> Received (counted: receipts fell short)
    #   204 flag blank, nothing received, plus a zero and a credit line -> Draft
    #   205 flag off, fractional 2.4 ordered (rounded to 2), 1 received -> Sent
    #   206 received-all (True), 5 of 10 received -> Received (counted: receipts fell short)
    #   207 flag off, 46.5 ordered (-> 47, half up), 0.5 + 1 received (-> 2, half up) -> Sent
    "tblPurchaseOrders.csv": _csv(
        "PurchaseOrderID,intProjectID,intVendorID,dtmOrderDate,memNote,blnRecievedAll",
        "201,10,1,2/1/2020 0:00:00,All here,-1",
        "202,10,1,2/2/2020 0:00:00,Some here,0",
        "203,10,1,2/3/2020 0:00:00,Nothing here,-1",
        "204,10,1,2/4/2020 0:00:00,Untouched with credit,",
        "205,10,1,2/5/2020 0:00:00,Fractional order,0",
        "206,10,1,2/6/2020 0:00:00,Short but done,True",
        "207,10,1,2/7/2020 0:00:00,Half quantities,0",
    ),
    "tblPurchaseOrdersMaterial.csv": _csv(
        "PurchaseOrderPartID,intPurchaseOrderID,intProductID,chrProductDescription,intQtyOrdered,"
        "curUnitPrice,intQtyReceived1,intQtyReceived2,intQtyReceived3",
        "301,201,,Hinges,4,5.00,4,0,0",
        "302,202,,Locks,10,6.00,1,2,0",          # 1 + 2 received across two slots
        "303,203,,Closers,2,7.00,0,0,0",
        "304,204,,Strikes,5,8.00,0,0,0",         # the untouched PO's only positive line
        "305,204,,Quoted only,0,8.00,0,0,0",     # zero line: nothing to receive
        "306,204,,Returned strike,-1,8.00,0,0,0",  # credit line: nothing to receive
        "307,205,,Kick plates,2.4,9.00,1,0,0",   # fractional: rounds to 2, reported
        "308,206,,Pulls,10,4.00,3,2,0",          # 5 of 10 on record, flag says all received
        "309,207,,Screws,46.5,0.10,0.5,1,0",     # half quantities: 46.5 -> 47 ordered, 1.5 -> 2 received
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


def _pos_by_id(db) -> dict[int, PurchaseOrder]:
    """Load the imported purchase orders keyed by Vision PurchaseOrderID.

    Args:
        db: An open SQLAlchemy session.

    Returns:
        ``{legacy PurchaseOrderID: PurchaseOrder}`` for every migrated PO.
    """
    rows = db.query(PurchaseOrder).filter(PurchaseOrder.legacy_source == "tblPurchaseOrders").all()
    return {po.legacy_id: po for po in rows}


def _lines_by_id(owner) -> dict:
    """Key a quote's or PO's imported lines by their own Vision line id.

    Args:
        owner: A loaded ``Quote`` or ``PurchaseOrder``.

    Returns:
        ``{legacy line id: line}``, so a test can pick out one line by its fixture id.
    """
    return {li.legacy_id: li for li in owner.line_items}   # ids are unique per fixture


def test_import_derives_quote_status_from_vision_ship_data():
    result = _upload()
    assert result["success"] is True
    assert result["counts"]["quotes"] == 15
    # The operator sees the split up front:
    # closed = 102, 103, 107, 108, 109, 110, 114; open = 101, 104, 105, 106, 111, 112, 113, 115.
    assert result["counts"]["quotes_closed"] == 7
    assert result["counts"]["quotes_open"] == 8
    assert result["counts"]["quotes_force_closed"] == 2              # 103 by text, 109 by flag
    # Only WO 104's misc line: a finished job's no-data line (2071) is done, not "missing data".
    assert result["counts"]["quote_lines_without_ship_data"] == 1
    assert any("no ship quantities" in w for w in result["warnings"])

    db = SessionLocal()
    try:
        quotes = _quotes_by_wo(db)
        assert set(quotes) == set(range(101, 116))                    # 101 .. 115

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


def test_import_closes_work_orders_vision_finished():
    result = _upload()
    # 108 (no lines) and 109 (only zero / credit lines) are Closed with nothing shipped: reported.
    assert any(
        w.startswith("Quotes: 2 finished in Vision with nothing shipped on record")
        for w in result["warnings"]
    ), result["warnings"]

    db = SessionLocal()
    try:
        quotes = _quotes_by_wo(db)

        # blnAllShipped = -1 beats lines that say 2 of 10 shipped: every positive line done.
        assert quotes[107].status == "Closed"
        lines = _lines_by_id(quotes[107])
        assert (lines[1071].quantity, lines[1071].qty_fulfilled, lines[1071].qty_pending) == (10, 10, 0)
        assert (lines[2071].qty_fulfilled, lines[2071].qty_pending) == (3, 0)    # no ship data, still done

        # blnAllShipped = True with no lines at all -> still Closed (the line rule alone can't).
        assert quotes[108].status == "Closed"
        assert quotes[108].line_items == []

        # Force-closed by flag, only a zero line and a credit line -> Closed.
        assert quotes[109].status == "Closed"
        lines = _lines_by_id(quotes[109])
        assert (lines[1091].quantity, lines[1091].qty_fulfilled, lines[1091].qty_pending) == (0, 0, 0)
        # Credit on a finished job counts as processed: fulfilled at its own (negative) quantity.
        assert (lines[3091].quantity, lines[3091].qty_fulfilled, lines[3091].qty_pending) == (-2, -2, 0)
    finally:
        db.close()


def test_import_keeps_zero_and_credit_lines_out_of_pending():
    _upload()
    db = SessionLocal()
    try:
        # Open job: its one positive line shipped in full; a zero line and two credit
        # lines ride along with nothing pending, so the quote closes by its lines.
        q110 = _quotes_by_wo(db)[110]
        assert q110.status == "Closed"
        lines = _lines_by_id(q110)
        assert (lines[2101].qty_fulfilled, lines[2101].qty_pending) == (4, 0)
        # Zero line: kept at 0 (was floored to 1), nothing fulfilled or pending.
        assert (lines[1101].quantity, lines[1101].qty_fulfilled, lines[1101].qty_pending) == (0, 0, 0)
        # Credit Vision processed (shipped total -3) -> fulfilled at -3, never pending.
        assert (lines[1102].quantity, lines[1102].qty_fulfilled, lines[1102].qty_pending) == (-3, -3, 0)
        # Credit never processed on an open job -> nothing fulfilled, still never pending.
        assert (lines[3101].quantity, lines[3101].qty_fulfilled, lines[3101].qty_pending) == (-1, 0, 0)
    finally:
        db.close()


def test_imported_rows_load_through_the_read_endpoints():
    """Every imported quote, PO and labour item opens, zero and credit lines included.

    Those rows break the input rules (quantity above 0, at most 2-decimal hours), and the
    read models used to apply the same rules, so one such row failed its whole page.
    """
    _upload()
    db = SessionLocal()
    try:
        quote_ids = [q.id for q in _quotes_by_wo(db).values()]
        po_ids = [po.id for po in _pos_by_id(db).values()]
    finally:
        db.close()
    for qid in quote_ids:                                   # WO 110 carries a 0 and two credit lines
        assert client.get(f"/quotes/{qid}").status_code == 200, qid
    for pid in po_ids:                                      # PO 204 carries a 0 and a credit line
        assert client.get(f"/purchase-orders/{pid}").status_code == 200, pid
    assert client.get("/quotes/").status_code == 200        # lists load every row at once
    assert client.get("/purchase-orders/").status_code == 200
    r = client.get("/labor/")
    assert r.status_code == 200, r.text
    short = next(l for l in r.json() if l["description"] == "Short call")
    assert short["hours"] == 0.08                           # rounded to what the app can hold
    assert round(short["hours"] * short["rate"], 2) == 6.81  # cost unchanged by the rounding


def test_read_leniency_does_not_loosen_input():
    """A quote line or PO line still cannot be entered at 0 or below."""
    _upload()
    db = SessionLocal()
    try:
        quote_id = _quotes_by_wo(db)[101].id
        po_id = next(iter(_pos_by_id(db).values())).id
    finally:
        db.close()
    for qty in (0, -1):
        r = client.post(f"/quotes/{quote_id}/lines",
                        json={"item_type": "misc", "description": "[TEST]", "quantity": qty, "unit_price": 1})
        assert r.status_code == 422, (qty, r.text)
        r = client.post(f"/purchase-orders/{po_id}/lines",
                        json={"item_type": "misc", "description": "[TEST]", "quantity": qty, "unit_price": 1})
        assert r.status_code == 422, (qty, r.text)


def test_import_keeps_fractional_quote_quantities():
    _upload()
    db = SessionLocal()
    try:
        q111 = _quotes_by_wo(db)[111]
        # Something fulfilled, something pending -> Invoiced.
        assert q111.status == "Invoiced"
        lines = _lines_by_id(q111)
        # Misc 1.5 ordered, 0.5 shipped -> 0.5 fulfilled, 1.0 pending (was floored to whole numbers).
        assert (lines[3111].quantity, lines[3111].qty_fulfilled, lines[3111].qty_pending) == (1.5, 0.5, 1.0)
        # Labour 2.333 rounds to the app's 2 decimals, fully shipped.
        assert (lines[1111].quantity, lines[1111].qty_fulfilled, lines[1111].qty_pending) == (2.33, 2.33, 0)
    finally:
        db.close()


def test_import_settles_work_orders_whose_only_processing_was_a_credit():
    _upload()
    db = SessionLocal()
    try:
        quotes = _quotes_by_wo(db)
        # Not finished in Vision, its only line a processed credit: the app's rule alone
        # would say Draft (it counts positive fulfilment only). Nothing pending -> Closed.
        assert quotes[114].status == "Closed"
        (li,) = quotes[114].line_items
        assert (li.quantity, li.qty_fulfilled, li.qty_pending) == (-2, -2, 0)

        # Same, but a positive line is still pending and there is a client PO: the app's
        # rule alone would say Work Order. Something processed, something left -> Invoiced.
        assert quotes[115].status == "Invoiced"
        lines = _lines_by_id(quotes[115])
        assert (lines[1151].quantity, lines[1151].qty_fulfilled, lines[1151].qty_pending) == (-1, -1, 0)
        assert (lines[2151].quantity, lines[2151].qty_fulfilled, lines[2151].qty_pending) == (3, 0, 3)
    finally:
        db.close()


def test_import_dates_work_order_by_entry_when_start_is_blank():
    _upload()
    db = SessionLocal()
    try:
        quotes = _quotes_by_wo(db)
        # Blank dtmDateStarted -> dtmDateEntered, not the time of the import.
        assert quotes[112].created_at == datetime(2020, 3, 15)
        # A start date still wins when present.
        assert quotes[101].created_at == datetime(2020, 1, 3)
    finally:
        db.close()


def test_import_keeps_projects_with_unknown_customer():
    result = _upload()
    # Each orphan project is named in a warning, not skipped.
    for project_id, client_id in ((12, 99), (13, 98)):
        assert any(
            w.startswith(f"Projects: ProjectID {project_id} has unknown ClientID {client_id}")
            for w in result["warnings"]
        ), result["warnings"]
    assert not any("skipped ProjectID" in w for w in result["warnings"])   # the old skip is gone
    assert not any("unknown PojectID" in w for w in result["warnings"])    # so no work order is dropped

    db = SessionLocal()
    try:
        # One placeholder customer, created once and shared by both unknown ClientIDs.
        (placeholder,) = db.query(Profile).filter(Profile.name == "Unknown Customer (Legacy)").all()
        assert placeholder.type == ProfileType.customer
        orphans = db.query(Project).filter(Project.legacy_id.in_([12, 13])).all()
        assert {p.legacy_id for p in orphans} == {12, 13}
        assert {p.customer_id for p in orphans} == {placeholder.id}
        # The orphan project's work order imported with it.
        q113 = _quotes_by_wo(db)[113]
        assert q113.project_id == next(p.id for p in orphans if p.legacy_id == 12)
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
    assert result["counts"]["purchase_orders"] == 7
    # Received = 201, 203, 206 (flag); Sent = 202, 205, 207 (partial receipts); Draft = 204 (none).
    assert result["counts"]["purchase_orders_received"] == 3
    assert result["counts"]["purchase_orders_sent"] == 3
    assert result["counts"]["purchase_orders_draft"] == 1

    db = SessionLocal()
    try:
        pos = _pos_by_id(db)
        for po in pos.values():
            assert po.legacy_imported is True
        # Flag off: receipts summed across the three Vision slots (1 + 2) -> partial -> Sent.
        assert pos[202].status is POStatus.sent
        (li,) = pos[202].line_items
        assert (li.qty_received, li.qty_pending) == (3, 7)
        assert li.legacy_source == "tblPurchaseOrdersMaterial" and li.legacy_id == 302

        # Flag blank, nothing received: the zero and credit lines must not make it look
        # partly received, so the PO stays Draft.
        assert pos[204].status is POStatus.draft
        lines = _lines_by_id(pos[204])
        assert (lines[304].quantity, lines[304].qty_received, lines[304].qty_pending) == (5, 0, 5)
        assert (lines[305].quantity, lines[305].qty_received, lines[305].qty_pending) == (0, 0, 0)
        assert (lines[306].quantity, lines[306].qty_received, lines[306].qty_pending) == (-1, 0, 0)
    finally:
        db.close()


def test_import_lands_received_all_pos_received():
    result = _upload()
    # 203 (nothing on record) and 206 (5 of 10 on record) fell short of their flag: reported.
    assert any(
        w.startswith("Purchase orders: 2 marked received-all") for w in result["warnings"]
    ), result["warnings"]

    db = SessionLocal()
    try:
        pos = _pos_by_id(db)
        # Receipts match the flag: Received, line untouched.
        assert pos[201].status is POStatus.received
        (li,) = pos[201].line_items
        assert (li.qty_received, li.qty_pending) == (4, 0)
        # Flag set (-1), nothing received on record: Vision's verdict wins, line marked received.
        assert pos[203].status is POStatus.received
        (li,) = pos[203].line_items
        assert (li.quantity, li.qty_received, li.qty_pending) == (2, 2, 0)
        # Flag set (True), 3 + 2 of 10 on record: line raised to the full 10.
        assert pos[206].status is POStatus.received
        (li,) = pos[206].line_items
        assert (li.quantity, li.qty_received, li.qty_pending) == (10, 10, 0)
    finally:
        db.close()


def test_import_rounds_fractional_po_quantities():
    result = _upload()
    # PO columns are whole numbers: fractions round (halves up), and the operator is told which lines.
    po_warnings = [w for w in result["warnings"] if w.startswith("PO lines: fractional quantities rounded")]
    assert len(po_warnings) == 1, result["warnings"]
    assert "307 (2.4 -> 2)" in po_warnings[0]                      # names the PurchaseOrderPartID
    assert "309 (46.5 -> 47)" in po_warnings[0]                    # half rounds up, not to even

    db = SessionLocal()
    try:
        pos = _pos_by_id(db)
        assert pos[205].status is POStatus.sent                      # 1 of 2 received
        (li,) = pos[205].line_items
        assert (li.quantity, li.qty_received, li.qty_pending) == (2, 1, 1)
        # 46.5 ordered -> 47; 0.5 + 1 received = 1.5 -> 2 (halves up); 45 still pending -> Sent.
        assert pos[207].status is POStatus.sent
        (li,) = pos[207].line_items
        assert (li.quantity, li.qty_received, li.qty_pending) == (47, 2, 45)
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
        assert db.query(Quote).filter(Quote.legacy_source == "tblServiceRecords").count() == 15
        assert db.query(QuoteLineItem).filter(QuoteLineItem.legacy_source.isnot(None)).count() == 20
        assert db.query(POLineItem).filter(POLineItem.legacy_source.isnot(None)).count() == 9
        # The unknown-customer placeholder is rebuilt per run, never duplicated.
        assert db.query(Profile).filter(Profile.name == "Unknown Customer (Legacy)").count() == 1
        # Legacy keys stayed unique across the two runs (no duplicate (source, id) pairs).
        pairs = db.query(QuoteLineItem.legacy_source, QuoteLineItem.legacy_id).filter(
            QuoteLineItem.legacy_id.isnot(None)
        ).all()
        assert len(pairs) == len(set(pairs))
    finally:
        db.close()
