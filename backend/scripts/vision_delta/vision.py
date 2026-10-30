"""Read a Vision CSV export into the records Velocity should hold for it.

Each table is mapped field for field the way the Vision import
(``routes/migration.py``) maps it, using that module's own parsing and
close-state helpers, so "what Velocity should hold" means exactly what the
import would have written. Nothing here touches a database.

Every record keeps its Vision id (``vid``) and the Vision dates the compare
uses to tell recent Vision changes apart from old ones.
"""
from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from routes.migration import (
    clean_currency,
    line_close_state,
    line_quantity,
    opt_int,
    opt_qty,
    parse_bool,
    parse_csv,
    parse_date,
    safe_float,
    safe_int,
    safe_str,
    whole_po_quantity,
    wo_prefixed_description,
    workorder_closed,
    workorder_created,
    workorder_force_closed,
)
from routes.purchase_orders import compute_po_status
from routes.quotes import compute_status_from_lines

# Names the import gives records Vision points at but does not have
UNKNOWN_CUSTOMER = "Unknown Customer (Legacy)"
UNKNOWN_VENDOR = "Unknown Vendor (Legacy)"


def read_table(csv_dir: str, table: str) -> list[dict]:
    """Read one exported Vision table.

    Args:
        csv_dir: Folder holding the export (``tblX.csv`` files).
        table: Table name without extension, e.g. ``"tblProjects"``.

    Returns:
        The table's rows as dicts keyed by Access field name.

    Raises:
        FileNotFoundError: The export has no file for this table.
    """
    with open(os.path.join(csv_dir, f"{table}.csv"), "rb") as f:
        return parse_csv(f.read())                         # same decoding as the import (BOM, cp1252 fallback)


def _address(row: dict) -> str:
    """Join address, city and province the way the import does."""
    parts = [safe_str(row.get(k, "")) for k in ("chrAddress", "chrCity", "chrProvince")]
    return ", ".join(p for p in parts if p)                # blanks dropped


def _phones(*pairs: tuple[str, str]) -> list[tuple[str, str]]:
    """Phone list for a contact: (type, number) for each non-blank number."""
    return [(kind, num) for kind, num in pairs if num]


def load_categories(rows: list[dict]) -> list[dict]:
    """Categories, one per Velocity type (an "Application & Material" row gives two).

    Args:
        rows: tblPartsCategories rows.

    Returns:
        ``{vid, type, name}`` records.
    """
    kinds = {"Application": ["labor"], "Material": ["part"], "Application & Material": ["part", "labor"]}
    out = []
    for row in rows:
        vid, name = safe_int(row.get("CategoryID", "")), safe_str(row.get("chrCategoryName", ""))
        if not vid or not name:
            continue                                       # the import skips these
        for kind in kinds.get(safe_str(row.get("chrCategoryType", "")), []):   # unknown type -> skipped
            out.append({"vid": vid, "type": kind, "name": name})
    return out


def load_customers(rows: list[dict]) -> list[dict]:
    """Customers with their (up to two) contacts.

    Args:
        rows: tblClients rows.

    Returns:
        ``{vid, type, name, pst, address, postal_code, contacts}`` records.
    """
    out = []
    for row in rows:
        vid = safe_int(row.get("Client ID", ""))
        if not vid:
            continue
        contacts = []
        for sfx in ("", "2"):                              # Vision keeps two contacts per client
            name = f"{safe_str(row.get('chrFirstName' + sfx, ''))} {safe_str(row.get('chrLastName' + sfx, ''))}".strip()
            if name:
                contacts.append({
                    "name": name,
                    "job_title": safe_str(row.get("chrTitle" + sfx, "")) or None,
                    "email": safe_str(row.get("chrEmailAddress" + sfx, "")) or None,
                    "phones": _phones(("work", safe_str(row.get("chrPhoneNumber" + sfx, ""))),
                                      ("mobile", safe_str(row.get("chrCell" + sfx, "")))),
                })
        out.append({
            "vid": vid,
            "type": "customer",
            "name": safe_str(row.get("chrCompanyName", "")) or f"Unknown Customer {vid}",
            "pst": safe_str(row.get("chrProvincialTax", "")),
            "address": _address(row),
            "postal_code": safe_str(row.get("chrPostalCode", "")),
            "contacts": contacts,
        })
    return out


def load_vendors(rows: list[dict]) -> list[dict]:
    """Vendors with their one contact.

    Args:
        rows: tblVendors rows.

    Returns:
        ``{vid, type, name, pst, address, postal_code, contacts}`` records.
    """
    out = []
    for row in rows:
        vid = safe_int(row.get("VendorID", ""))
        if not vid:
            continue
        name = f"{safe_str(row.get('chrFirstName', ''))} {safe_str(row.get('chrLastName', ''))}".strip()
        contacts = [{
            "name": name,
            "job_title": None,                             # Vision vendors carry no title
            "email": safe_str(row.get("chrEmailAddress", "")).strip("'") or None,   # some emails are quoted
            "phones": _phones(("work", safe_str(row.get("chrPhoneNumber", "")))),
        }] if name else []
        out.append({
            "vid": vid,
            "type": "vendor",
            "name": safe_str(row.get("chrCompanyName", "")) or f"Unknown Vendor {vid}",
            "pst": safe_str(row.get("chrProvincialTax", "")),
            "address": _address(row),
            "postal_code": safe_str(row.get("chrPostalCode", "")),
            "contacts": contacts,
        })
    return out


def load_parts(rows: list[dict], categories: dict[int, str], vendors: dict[int, str]) -> list[dict]:
    """Inventory parts (``LM-`` labour+material combos are not parts, as in the import).

    Args:
        rows: tblMaterial rows.
        categories: Vision CategoryID -> category name.
        vendors: Vision VendorID -> vendor name.

    Returns:
        ``{vid, part_number, description, cost, markup_percent, category, vendor}`` records.
    """
    out = []
    for row in rows:
        vid, number = safe_int(row.get("ProductID", "")), safe_str(row.get("chrProductName", ""))
        if not vid or not number or number.upper().startswith("LM-"):
            continue
        out.append({
            "vid": vid,
            "part_number": number,
            "description": safe_str(row.get("chrProductDescription", "")) or number,
            "cost": clean_currency(row.get("curNetPrice", "")),
            "markup_percent": safe_float(row.get("intMarkup", "")),
            "category": categories.get(safe_int(row.get("intCategory", ""))),
            "vendor": vendors.get(safe_int(row.get("intVendor", ""))),
        })
    return out


def load_labour(rows: list[dict], categories: dict[int, str]) -> list[dict]:
    """Labour items, with hours held to 2 decimals and the rate adjusted to keep cost.

    Args:
        rows: tblApplication rows.
        categories: Vision CategoryID -> category name.

    Returns:
        ``{vid, description, hours, rate, markup_percent, category}`` records.
    """
    out = []
    for row in rows:
        vid, desc = safe_int(row.get("ProductID", "")), safe_str(row.get("chrProductDescription", ""))
        if not vid or not desc:
            continue
        hours_raw, net = safe_float(row.get("intTime", "")), clean_currency(row.get("curNetPrice", ""))
        if hours_raw > 0:
            hours = round(hours_raw, 2) or 0.01            # same rule as the import
            rate = net / hours                             # hours x rate = Vision net price
        else:
            hours, rate = 1, net
        out.append({
            "vid": vid, "description": desc, "hours": hours, "rate": rate,
            "markup_percent": safe_float(row.get("intMarkup", "")),
            "category": categories.get(safe_int(row.get("intCategory", ""))),
        })
    return out


def zone_description(row: dict, vid: int) -> str:
    """Misc item description built from a zone row, as the import builds it."""
    zone, distance = safe_str(row.get("chrZones", "")), safe_str(row.get("chrDistance", ""))
    if zone and distance:
        return f"{zone} - {distance}"
    return distance or zone or f"Zone {vid}"


def load_misc(rows: list[dict]) -> list[dict]:
    """Misc (zone) items.

    Args:
        rows: tblZones rows.

    Returns:
        ``{vid, description, unit_price, markup_percent}`` records.
    """
    out = []
    for row in rows:
        vid = safe_int(row.get("ZoneRateID", ""))
        if not vid:
            continue
        out.append({
            "vid": vid, "description": zone_description(row, vid),
            "unit_price": clean_currency(row.get("curNetPrice", "")),
            "markup_percent": safe_float(row.get("intMarkup", "")),
        })
    return out


def load_projects(rows: list[dict], customers: dict[int, str]) -> list[dict]:
    """Projects, with the UCA number formatted (and de-duplicated) as the import does.

    Args:
        rows: tblProjects rows, in file order (duplicate UCA numbers get a suffix in that order).
        customers: Vision Client ID -> customer name.

    Returns:
        ``{vid, uca, name, customer, created_on, status, ucsh, lead}`` records.
    """
    out, seen = [], set()
    for row in rows:
        vid = safe_int(row.get("ProjectID", ""))
        if not vid:
            continue
        raw = safe_str(row.get("UCAProjectNr", "")) or str(vid)
        try:
            uca = f"A{int(raw):04d}"                       # numeric -> "A" + 4 digits
        except ValueError:
            uca = raw                                      # already prefixed / not numeric
        if uca in seen:
            uca = f"{uca}-{vid}"                           # duplicate UCA number -> suffixed
        seen.add(uca)
        out.append({
            "vid": vid, "uca": uca,
            "name": safe_str(row.get("ProjectName", "")) or f"Project {vid}",
            "customer": customers.get(safe_int(row.get("ClientID", "")), UNKNOWN_CUSTOMER),
            "created_on": parse_date(row.get("dtmStartDate", "")),
            "status": "closed" if parse_bool(row.get("blnArchive")) else "active",   # Vision's archive flag = a finished job (#330)
            "ucsh": safe_str(row.get("UCSHProjectNr", "")) or None,
            "lead": safe_str(row.get("EmployeeID", "")) or None,
        })
    return out


# The three work-order line tables: (table, item type, inventory-id field, price field, description field)
QUOTE_LINE_TABLES = [
    ("tblWorkorderApplication", "labor", "intProductName", "curUnitPrice", "chrProductDescription"),
    ("tblWorkorderMaterial", "part", "intProductName", "curUnitPrice", "chrProductDescription"),
    ("tblWorkorderZones", "misc", "chrZones", "curPrice", "chrDistance"),
]


def load_quotes(csv_dir: str, projects: dict[int, str], refs: dict[str, dict[int, str]]) -> list[dict]:
    """Work orders as Velocity quotes, with their lines and Vision's close-state.

    Args:
        csv_dir: The export folder.
        projects: Vision ProjectID -> UCA number.
        refs: Per item type, Vision inventory id -> the key Velocity shows
            (part number, labour description, misc description).

    Returns:
        ``{wo, uca, created_at, work_description, client_po_number, closed,
        force_closed, status, entered, revised, lines}`` records. Each line is
        ``{vid, item_type, ref, description, quantity, unit_price, base_cost,
        qty_fulfilled, qty_pending, entered}``.
    """
    quotes: dict[int, dict] = {}
    for row in read_table(csv_dir, "tblServiceRecords"):
        wo, pid = safe_int(row.get("WorkorderID", "")), safe_int(row.get("PojectID", ""))
        if not wo or pid not in projects:
            continue                                       # the import skips these
        quotes[wo] = {
            "wo": wo, "uca": projects[pid],
            "created_at": workorder_created(row),
            "work_description": wo_prefixed_description(wo, row.get("memWorkDescription", "")),
            "client_po_number": safe_str(row.get("intPONumber", "")) or None,
            "closed": workorder_closed(row),               # Vision's finished verdict
            "force_closed": workorder_force_closed(row),
            "entered": parse_date(row.get("dtmDateEntered", "")),
            "revised": parse_date(row.get("dtmRevisionDate", "")),
            "lines": [],
        }
    for table, kind, ref_field, price_field, desc_field in QUOTE_LINE_TABLES:
        for row in read_table(csv_dir, table):
            q = quotes.get(safe_int(row.get("intWorkorderID", "")))
            if q is None:
                continue                                   # line of a skipped / unknown work order
            qty = line_quantity(row, "intQuantity")        # fraction, 0 and negative kept
            fulfilled, pending, _ = line_close_state(row, qty, q["closed"])
            q["lines"].append({
                "vid": opt_int(row.get("WorkorderPartID", "")),
                "item_type": kind,
                "item_vid": safe_int(row.get(ref_field, "")) or None,   # Vision id of the inventory item
                "ref": refs[kind].get(safe_int(row.get(ref_field, ""))),
                "description": safe_str(row.get(desc_field, "")) or None,
                "quantity": qty,
                "unit_price": clean_currency(row.get(price_field, "")),
                "base_cost": clean_currency(row.get("curNetPrice", "")),
                "qty_fulfilled": fulfilled,
                "qty_pending": pending,
                "entered": parse_date(row.get("dtmDateEntered", "")),
            })
    for q in quotes.values():
        q["status"] = quote_status(q)
    return list(quotes.values())


def quote_status(q: dict) -> str:
    """Status the import gives a work order (its step 11b).

    Args:
        q: A quote record from :func:`load_quotes`.

    Returns:
        "Closed" when Vision finished it, otherwise the app's status rule over
        its lines (credit-only fulfilment decided as the import decides it).
    """
    lines = [SimpleNamespace(**l) for l in q["lines"]]     # the rule reads attributes
    if q["closed"]:
        return "Closed"                                    # Vision's verdict wins
    from_lines = compute_status_from_lines(lines, q["client_po_number"])
    if from_lines in ("Draft", "Work Order") and any(l.qty_fulfilled < 0 for l in lines):
        return "Closed" if all(l.qty_pending == 0 for l in lines) else "Invoiced"   # credits only processed
    return from_lines


def load_pos(csv_dir: str, projects: dict[int, str], vendors: dict[int, str], parts: dict[int, str]) -> list[dict]:
    """Purchase orders with their lines, numbered per project the way the import numbers them.

    Args:
        csv_dir: The export folder.
        projects: Vision ProjectID -> UCA number.
        vendors: Vision VendorID -> vendor name.
        parts: Vision ProductID -> part number.

    Returns:
        ``{vid, number, uca, seq, vendor, created_at, work_description,
        received_all, status, lines}`` records (``number`` is the PO number
        Vision printed). Each line is ``{vid, part_number, description,
        quantity, vision_quantity, unit_price, qty_received, qty_pending,
        last_received}``.
    """
    by_project: dict[str, list[dict]] = {}
    pos: dict[int, dict] = {}
    for row in read_table(csv_dir, "tblPurchaseOrders"):
        vid, pid = safe_int(row.get("PurchaseOrderID", "")), safe_int(row.get("intProjectID", ""))
        if not vid or pid not in projects:
            continue
        po = {
            "vid": vid, "uca": projects[pid], "seq": None,
            "number": safe_str(row.get("chrPONumber", "")) or safe_str(row.get("intAutoPONumber", "")) or None,
            "vendor": vendors.get(safe_int(row.get("intVendorID", "")), UNKNOWN_VENDOR),
            "created_at": parse_date(row.get("dtmOrderDate", "")),
            "work_description": safe_str(row.get("memNote", "")) or None,
            "received_all": parse_bool(row.get("blnRecievedAll", "")) is True,   # sic: Vision's spelling
            "lines": [],
        }
        pos[vid] = po
        by_project.setdefault(po["uca"], []).append(po)
    for project_pos in by_project.values():               # per-project numbering: by order date, then id
        project_pos.sort(key=lambda p: (p["created_at"] or datetime.min, p["vid"]))
        for seq, po in enumerate(project_pos, start=1):
            po["seq"] = seq
    for row in read_table(csv_dir, "tblPurchaseOrdersMaterial"):
        po = pos.get(safe_int(row.get("intPurchaseOrderID", "")))
        if po is None:
            continue
        vision_qty = line_quantity(row, "intQtyOrdered")  # may be fractional in Vision
        qty = whole_po_quantity(vision_qty)                # PO lines are whole in Velocity
        received = whole_po_quantity(sum(opt_qty(row.get(f"intQtyReceived{n}")) or 0.0 for n in (1, 2, 3)))
        if qty <= 0:
            pending = 0                                    # zero / credit line: nothing to receive
        elif po["received_all"]:
            received, pending = max(received, qty), 0      # Vision says received all
        else:
            pending = max(0, qty - received)
        dates = [parse_date(row.get(f"dtmDateReceived{n}", "")) for n in (1, 2, 3)]
        po["lines"].append({
            "vid": opt_int(row.get("PurchaseOrderPartID", "")),
            "item_vid": safe_int(row.get("intProductID", "")) or None,   # Vision id of the part
            "part_number": parts.get(safe_int(row.get("intProductID", ""))),
            "description": safe_str(row.get("chrProductDescription", "")) or None,
            "quantity": qty, "vision_quantity": vision_qty,
            "unit_price": clean_currency(row.get("curUnitPrice", "")),
            "qty_received": received, "qty_pending": pending,
            "last_received": max((d for d in dates if d), default=None),   # latest receipt date
        })
    for po in pos.values():
        positive = [SimpleNamespace(**l) for l in po["lines"] if l["quantity"] > 0]   # zero/credit lines left out
        po["status"] = "Received" if po["received_all"] else compute_po_status(positive).value
    return list(pos.values())


def load(csv_dir: str) -> dict:
    """Read the whole export.

    Args:
        csv_dir: Folder holding the 13 exported tables.

    Returns:
        ``{categories, profiles, parts, labour, misc, projects, quotes, pos}``.
    """
    cat_rows = read_table(csv_dir, "tblPartsCategories")
    cat_names = {safe_int(r.get("CategoryID", "")): safe_str(r.get("chrCategoryName", "")) for r in cat_rows}
    customers = load_customers(read_table(csv_dir, "tblClients"))
    vendors = load_vendors(read_table(csv_dir, "tblVendors"))
    vendor_names = {v["vid"]: v["name"] for v in vendors}
    parts = load_parts(read_table(csv_dir, "tblMaterial"), cat_names, vendor_names)
    labour = load_labour(read_table(csv_dir, "tblApplication"), cat_names)
    misc = load_misc(read_table(csv_dir, "tblZones"))
    projects = load_projects(read_table(csv_dir, "tblProjects"), {c["vid"]: c["name"] for c in customers})
    project_uca = {p["vid"]: p["uca"] for p in projects}
    refs = {
        "part": {p["vid"]: p["part_number"] for p in parts},
        "labor": {l["vid"]: l["description"] for l in labour},
        "misc": {m["vid"]: m["description"] for m in misc},
    }
    return {
        "categories": load_categories(cat_rows),
        "profiles": customers + vendors,
        "parts": parts, "labour": labour, "misc": misc, "projects": projects,
        "quotes": load_quotes(csv_dir, project_uca, refs),
        "pos": load_pos(csv_dir, project_uca, vendor_names, refs["part"]),
    }


def newest(*dates: Optional[datetime]) -> Optional[datetime]:
    """Latest of the given dates, ignoring blanks."""
    return max((d for d in dates if d), default=None)
