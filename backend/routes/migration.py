"""
Legacy data migration endpoint.

Imports CSV exports from UC Vision (Access database) into UC Velocity.
Processing order respects FK dependencies. The entire import runs in a
single transaction — if anything fails, everything rolls back.

Close-state (issue #164): a quote's status is never hardcoded. A work order
Vision marks finished (``blnAllShipped`` or ``blnForceClosed``) arrives Closed
with every line done. Any other work order is decided by its lines: each line
reads Vision's shipped / back-ordered quantities to derive what is fulfilled and
what is still pending, and the app's own rule (``compute_status_from_lines``)
sets the status. So a job still open in Vision arrives open with its remaining
quantities ready to invoice. Purchase orders work the same way: Vision's
``blnRecievedAll`` lands a PO Received, otherwise its line receipts decide
(``compute_po_status``).

Quantities are read as Vision stores them: fractions kept (to 2 decimals), and
zero (quoted, not ordered) or negative (credit) lines kept as they are, with
nothing pending on them.

Every imported row also carries its Vision origin (``legacy_source`` = the
source table, ``legacy_id`` = the source row's primary key) and migrated quotes
and POs are flagged ``legacy_imported``.
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List, Optional
from datetime import datetime
import csv
import io
import re

from database import get_db
from models import (
    Category, Profile, ProfileType, Contact, ContactPhone, PhoneType,
    Part, Labor, Miscellaneous, Project, Quote, QuoteLineItem,
    PurchaseOrder, POLineItem, POStatus,
)
from routes.quotes import compute_status_from_lines      # the app's quote status rule
from routes.quotes import round_quantity                 # the app's 2-decimal quantity rounding
from routes.purchase_orders import compute_po_status     # the app's PO status rule

router = APIRouter(prefix="/migration", tags=["migration"])

BATCH_SIZE = 500

# The 13 CSV files we recognize, in processing order
EXPECTED_FILES = [
    "tblPartsCategories.csv",
    "tblClients.csv",
    "tblVendors.csv",
    "tblMaterial.csv",
    "tblApplication.csv",
    "tblZones.csv",
    "tblProjects.csv",
    "tblServiceRecords.csv",
    "tblWorkorderApplication.csv",
    "tblWorkorderMaterial.csv",
    "tblWorkorderZones.csv",
    "tblPurchaseOrders.csv",
    "tblPurchaseOrdersMaterial.csv",
]


def parse_csv(content: bytes) -> list[dict]:
    """Parse CSV bytes into list of dicts, handling BOM and legacy encodings."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def clean_currency(val: str) -> float:
    """Strip '$', ',', and whitespace from a currency string, return float.
    Handles accounting-style negatives: (722.68) → -722.68"""
    if not val:
        return 0.0
    cleaned = val.replace("$", "").replace(",", "").strip()
    if not cleaned:
        return 0.0
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    return float(cleaned)


def safe_int(val: str, default: int = 0) -> int:
    """Parse a value to int, rounding floats."""
    if not val or not val.strip():
        return default
    try:
        return round(float(val.strip()))
    except (ValueError, TypeError):
        return default


def safe_float(val: str, default: float = 0.0) -> float:
    """Parse a value to float."""
    if not val or not val.strip():
        return default
    try:
        return float(val.strip())
    except (ValueError, TypeError):
        return default


def parse_date(val: str) -> datetime | None:
    """Parse Access date formats like '11/12/2004 0:00:00'."""
    if not val or not val.strip():
        return None
    val = val.strip()
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


def safe_str(val: str | None, default: str = "") -> str:
    """Return stripped string or default."""
    if val is None:
        return default
    return val.strip() or default


def wo_prefixed_description(legacy_wo_id: int, raw_desc: str | None) -> str:
    """Prepend the legacy UC Vision work-order number to a quote's work description.

    Issue #54: in UC Vision the WorkorderID was the human-facing quote-number.
    Velocity replaced it with its own quote_number, so the legacy reference is
    preserved at the very start of the migrated work description as "[WO {id}]".
    A blank description collapses to just the "[WO {id}]" tag.
    """
    prefix = f"[WO {legacy_wo_id}]"
    desc = safe_str(raw_desc)
    return f"{prefix} {desc}" if desc else prefix


# --------------------------------------------------------------------------- #
# Close-state helpers (issue #164)
# --------------------------------------------------------------------------- #
# UC Vision records whether a work order is finished in fields the original
# import ignored: ``chrStatus`` / ``blnForceClosed`` on the work-order header and
# the shipped / back-ordered quantities on every line. These helpers read them so
# an imported quote carries Vision's real state instead of a hardcoded "Closed".

def parse_bool(val: str | None) -> Optional[bool]:
    """Parse an Access yes/no value from a CSV cell.

    Access exports TRUE as ``-1`` or ``1`` (or True/Yes) and FALSE as ``0`` (or
    False/No). Anything else means "not recorded".

    Args:
        val: The raw CSV cell.

    Returns:
        True, False, or None when the cell is blank or unrecognised.
    """
    s = safe_str(val).lower()                              # normalise for matching
    if s in ("1", "-1", "true", "yes", "y", "t"):          # Access TRUE encodings
        return True
    if s in ("0", "false", "no", "n", "f"):                # Access FALSE encodings
        return False
    return None                                            # blank / unknown -> not recorded


def opt_int(val: str | None) -> Optional[int]:
    """Parse an int, returning None (not 0) for a blank or unparseable cell.

    Used where "nothing recorded" must be told apart from a real zero: shipped
    quantities and legacy primary keys.

    Args:
        val: The raw CSV cell.

    Returns:
        The rounded integer, or None.
    """
    s = safe_str(val)
    if not s:                                              # blank -> not recorded
        return None
    try:
        return round(float(s))                             # Access may export "3.0"
    except (ValueError, TypeError):
        return None                                        # garbage -> not recorded


def opt_qty(val: str | None) -> Optional[float]:
    """Parse a Vision quantity, keeping its fraction, sign and zero.

    Vision stores quantities as Access ``Single`` (fractional) even where the
    field name starts with ``int``, so they are read as floats and rounded to the
    2 decimals the app stores.

    Args:
        val: The raw CSV cell.

    Returns:
        The quantity rounded to 2 decimals, or None for a blank or unparseable cell.
    """
    s = safe_str(val).replace(",", "")                     # tolerate "1,200"
    if not s:                                              # blank -> not recorded
        return None
    try:
        return round_quantity(float(s))                    # 1.5 stays 1.5; 0 and -2 stay as they are
    except (ValueError, TypeError):
        return None                                        # garbage -> not recorded


def line_quantity(row: dict, field: str) -> float:
    """Ordered quantity of an imported line, as Vision has it.

    Args:
        row: The line's CSV row.
        field: The quantity field name (``intQuantity`` / ``intQtyOrdered``).

    Returns:
        The quantity; a blank cell falls back to 1, as the import always did.
    """
    qty = opt_qty(row.get(field))
    return 1.0 if qty is None else qty                     # blank never occurs in practice; keep the old default


def derive_line_fulfillment(row: dict, quantity: float) -> Optional[tuple[float, float]]:
    """Derive ``(qty_fulfilled, qty_pending)`` from a work-order line's ship fields.

    Signals, in priority order:
      1. ``intTotalShippedQuantity`` - the running total shipped for the line.
      2. ``intShipQuantity`` - a single shipment quantity, if no total.
      3. ``intQuantityBO`` - back-ordered; shipped = quantity - back-ordered.

    Args:
        row: A tblWorkorderApplication / tblWorkorderMaterial / tblWorkorderZones
            CSV row.
        quantity: The line's ordered quantity; positive (the caller handles
            zero and negative lines).

    Returns:
        ``(fulfilled, pending)`` clamped to ``[0, quantity]``, or None when the row
        carries no usable signal, so the caller can tell "no ship data" apart from
        "zero shipped".
    """
    shipped = opt_qty(row.get("intTotalShippedQuantity"))   # 1. running total shipped
    if shipped is None:
        shipped = opt_qty(row.get("intShipQuantity"))       # 2. single shipment quantity
    if shipped is None:
        # 3. Infer from back-order, but ONLY a POSITIVE back-order is evidence: a
        # line reaches this branch precisely because neither shipped field is set,
        # so a bare intQuantityBO=0 (Vision's default/empty) is NOT proof the line
        # fully shipped -- treating it so would silently close never-shipped lines.
        # A back-order > 0 does imply "ordered minus back-ordered was shipped".
        backordered = opt_qty(row.get("intQuantityBO"))
        if backordered and backordered > 0:                 # positive back-order only (0/None/negative -> no signal)
            shipped = quantity - backordered
    if shipped is None:                                    # nothing recorded at all
        return None
    shipped = max(0.0, min(shipped, quantity))             # clamp bad legacy data (over-shipped, negative)
    return round_quantity(shipped), round_quantity(quantity - shipped)  # no float noise in pending


def workorder_created(row: dict) -> Optional[datetime]:
    """When a work order was created in Vision.

    ``dtmDateStarted`` is the usual date; some work orders (progress billing
    invoices mostly) leave it blank but still carry ``dtmDateEntered``.

    Args:
        row: The tblServiceRecords CSV row.

    Returns:
        The start date, else the entry date, else None.
    """
    return parse_date(row.get("dtmDateStarted", "")) or parse_date(row.get("dtmDateEntered", ""))


def whole_po_quantity(qty: float) -> int:
    """Round a Vision PO quantity to the whole number Velocity's PO lines hold.

    Halves round away from zero (2.5 -> 3, not Python's 2), and a nonzero
    quantity never becomes 0, so a 0.4 line still has something to receive.

    Args:
        qty: The quantity as Vision has it.

    Returns:
        The whole-number quantity.
    """
    if qty == 0:
        return 0
    whole = int(abs(qty) + 0.5)                            # half away from zero
    return (max(1, whole)) * (1 if qty > 0 else -1)        # keep sign, never collapse to 0


def workorder_closed(row: dict) -> bool:
    """Whether Vision considers a work order finished.

    Vision sets ``blnAllShipped`` on every finished work order: closed with all
    items shipped, credit memo, progress billing invoice and force closed alike.
    A force-close counts too (see :func:`workorder_force_closed`). A finished
    work order is closed in Velocity even when its lines say otherwise, because
    the header is Vision's own verdict on the job.

    Args:
        row: The tblServiceRecords CSV row.

    Returns:
        True if finished in Vision, else False.
    """
    if parse_bool(row.get("blnAllShipped")) is True:       # Vision's finished flag
        return True
    return workorder_force_closed(row)                      # manual override also finishes it


def workorder_force_closed(row: dict) -> bool:
    """Whether a work-order header was manually force-closed in Vision.

    Force-close is a business override: staff marked the job done even though
    not every line shipped, so it must trump the per-line quantities. Vision
    signals it two ways, either of which counts: the ``blnForceClosed`` flag, or
    a ``chrStatus`` beginning with "Force Closed" (the source has free-text
    variants like "Force Closed by DPowell.").

    Args:
        row: The tblServiceRecords CSV row.

    Returns:
        True if force-closed, else False.
    """
    if parse_bool(row.get("blnForceClosed")) is True:                        # explicit flag
        return True
    return safe_str(row.get("chrStatus")).lower().startswith("force closed")  # free-text variant


def line_close_state(row: dict, quantity: float, closed: bool) -> tuple[float, float, bool]:
    """Fulfilled / pending split for one imported work-order line.

    Args:
        row: The line's CSV row.
        quantity: The ordered quantity, as Vision has it.
        closed: True when the owning work order is finished in Vision
            (see :func:`workorder_closed`).

    Returns:
        ``(qty_fulfilled, qty_pending, ship_data_present)``. A zero line (quoted,
        not ordered) has nothing fulfilled or pending. A negative line (a credit)
        has nothing pending, so it never shows as left to invoice; it counts as
        fulfilled when Vision shipped it or the job is finished. A finished work
        order makes every positive line fulfilled. A line with no ship data is
        left fully pending (never claim work was done that Vision did not record).
    """
    if quantity == 0:                                      # quoted, not ordered: nothing to invoice
        return 0.0, 0.0, True
    if quantity < 0:                                       # credit line: never pending
        shipped = opt_qty(row.get("intTotalShippedQuantity"))   # running total wins, a recorded 0 included
        if shipped is None:                                # only when no total was recorded
            shipped = opt_qty(row.get("intShipQuantity"))  # (open credits carry intShipQuantity = qty, total 0)
        done = closed or bool(shipped)                     # processed in Vision?
        return (quantity if done else 0.0), 0.0, True
    if closed:                                             # finished header -> whole line done
        return quantity, 0.0, True
    derived = derive_line_fulfillment(row, quantity)
    if derived is None:                                    # nothing recorded -> nothing claimed done
        return 0, quantity, False
    fulfilled, pending = derived
    return fulfilled, pending, True


def flush_batch(db: Session, batch: list, id_map: dict):
    """Flush a batch of (legacy_id, orm_object) and populate id_map."""
    if not batch:
        return
    db.flush()
    for legacy_id, obj in batch:
        id_map[legacy_id] = obj.id
    batch.clear()


@router.post("/import")
async def import_legacy_data(
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """
    Import legacy UC Vision CSV data.

    Accepts multiple CSV files via multipart form upload.
    Wipes all existing data (except cost_codes, company_settings)
    and imports from the CSV files in FK-dependency order.
    """
    # Read all uploaded files into a dict keyed by filename
    file_contents: dict[str, bytes] = {}
    skipped_files: list[str] = []

    for f in files:
        fname = f.filename or ""
        content = await f.read()
        if fname in [ef for ef in EXPECTED_FILES]:
            file_contents[fname] = content
        else:
            skipped_files.append(fname)

    warnings: list[str] = []
    errors: list[str] = []
    counts: dict[str, int] = {}

    try:
        # === WIPE existing data ===
        # Note: miscellaneous is NOT truncated — system items (Parking, Travel Distance)
        # must survive. Non-system misc items are selectively deleted below.
        db.execute(text(
            "TRUNCATE categories, profiles, parts, labor, projects CASCADE"
        ))
        db.execute(text("DELETE FROM miscellaneous WHERE is_system_item = false"))

        # Reset sequences
        sequences = [
            "categories_id_seq",
            "profiles_id_seq",
            "contacts_id_seq",
            "contact_phones_id_seq",
            "parts_id_seq",
            "labor_id_seq",
            "projects_id_seq",
            "quotes_id_seq",
            "quote_line_items_id_seq",
            "purchase_orders_id_seq",
            "po_line_items_id_seq",
        ]
        for seq in sequences:
            try:
                db.execute(text(f"ALTER SEQUENCE {seq} RESTART WITH 1"))
            except Exception:
                pass  # Sequence may not exist

        # ID maps: legacy_id -> new_id
        cat_map_part: dict[int, int] = {}
        cat_map_labor: dict[int, int] = {}
        customer_map: dict[int, int] = {}
        vendor_map: dict[int, int] = {}
        part_map: dict[int, int] = {}
        labor_map: dict[int, int] = {}
        misc_map: dict[int, int] = {}
        project_map: dict[int, int] = {}
        quote_map: dict[int, int] = {}
        po_map: dict[int, int] = {}

        # === 1. Categories ===
        if "tblPartsCategories.csv" in file_contents:
            rows = parse_csv(file_contents["tblPartsCategories.csv"])
            count = 0
            batch_part: list[tuple[int, Category]] = []
            batch_labor: list[tuple[int, Category]] = []

            for row in rows:
                legacy_id = safe_int(row.get("CategoryID", ""))
                name = safe_str(row.get("chrCategoryName", ""))
                cat_type = safe_str(row.get("chrCategoryType", ""))

                if not legacy_id or not name:
                    warnings.append(f"Categories: skipped row with empty ID or name")
                    continue

                if cat_type == "Application":
                    cat = Category(name=name, type="labor", legacy_source="tblPartsCategories", legacy_id=legacy_id)
                    db.add(cat)
                    batch_labor.append((legacy_id, cat))
                    count += 1
                elif cat_type == "Material":
                    cat = Category(name=name, type="part", legacy_source="tblPartsCategories", legacy_id=legacy_id)
                    db.add(cat)
                    batch_part.append((legacy_id, cat))
                    count += 1
                elif cat_type == "Application & Material":
                    cat_p = Category(name=name, type="part", legacy_source="tblPartsCategories", legacy_id=legacy_id)
                    db.add(cat_p)
                    batch_part.append((legacy_id, cat_p))

                    cat_l = Category(name=name, type="labor", legacy_source="tblPartsCategories", legacy_id=legacy_id)
                    db.add(cat_l)
                    batch_labor.append((legacy_id, cat_l))
                    count += 2
                else:
                    warnings.append(f"Categories: unknown type '{cat_type}' for ID {legacy_id}")
                    continue

                if len(batch_part) + len(batch_labor) >= BATCH_SIZE:
                    flush_batch(db, batch_part, cat_map_part)
                    flush_batch(db, batch_labor, cat_map_labor)

            flush_batch(db, batch_part, cat_map_part)
            flush_batch(db, batch_labor, cat_map_labor)
            counts["categories"] = count

        # === 2. Customers (tblClients) ===
        if "tblClients.csv" in file_contents:
            rows = parse_csv(file_contents["tblClients.csv"])
            count = 0
            batch: list[tuple[int, Profile]] = []
            # Pending contacts: list of (profile_obj, contact_data_list)
            pending_contacts: list[tuple[Profile, list[dict]]] = []

            for row in rows:
                legacy_id = safe_int(row.get("Client ID", ""))
                name = safe_str(row.get("chrCompanyName", ""))

                if not legacy_id:
                    warnings.append(f"Customers: skipped row with empty ID")
                    continue

                if not name:
                    name = f"Unknown Customer {legacy_id}"

                # Build address
                addr_parts = [
                    safe_str(row.get("chrAddress", "")),
                    safe_str(row.get("chrCity", "")),
                    safe_str(row.get("chrProvince", "")),
                ]
                address = ", ".join(p for p in addr_parts if p)

                profile = Profile(
                    name=name,
                    type=ProfileType.customer,
                    pst=safe_str(row.get("chrProvincialTax", "")),
                    address=address,
                    postal_code=safe_str(row.get("chrPostalCode", "")),
                    legacy_source="tblClients",   # Vision table this row came from
                    legacy_id=legacy_id,          # Vision "Client ID"
                )
                db.add(profile)
                batch.append((legacy_id, profile))
                count += 1

                # Collect contact data for this profile
                contacts_data = []
                first1 = safe_str(row.get("chrFirstName", ""))
                last1 = safe_str(row.get("chrLastName", ""))
                contact_name1 = f"{first1} {last1}".strip()
                if contact_name1:
                    contacts_data.append({
                        "name": contact_name1,
                        "job_title": safe_str(row.get("chrTitle", "")) or None,
                        "email": safe_str(row.get("chrEmailAddress", "")) or None,
                        "phone": safe_str(row.get("chrPhoneNumber", "")),
                        "cell": safe_str(row.get("chrCell", "")),
                    })
                first2 = safe_str(row.get("chrFirstName2", ""))
                last2 = safe_str(row.get("chrLastName2", ""))
                contact_name2 = f"{first2} {last2}".strip()
                if contact_name2:
                    contacts_data.append({
                        "name": contact_name2,
                        "job_title": safe_str(row.get("chrTitle2", "")) or None,
                        "email": safe_str(row.get("chrEmailAddress2", "")) or None,
                        "phone": safe_str(row.get("chrPhoneNumber2", "")),
                        "cell": safe_str(row.get("chrCell2", "")),
                    })
                if contacts_data:
                    pending_contacts.append((profile, contacts_data))

                if len(batch) >= BATCH_SIZE:
                    flush_batch(db, batch, customer_map)
                    # Create contacts for flushed profiles (they now have IDs)
                    for prof, cdata_list in pending_contacts:
                        for cdata in cdata_list:
                            contact = Contact(
                                profile_id=prof.id,
                                name=cdata["name"],
                                job_title=cdata["job_title"],
                                email=cdata["email"],
                            )
                            db.add(contact)
                            db.flush()
                            if cdata["phone"]:
                                db.add(ContactPhone(contact_id=contact.id, type=PhoneType.work, number=cdata["phone"]))
                            if cdata["cell"]:
                                db.add(ContactPhone(contact_id=contact.id, type=PhoneType.mobile, number=cdata["cell"]))
                    pending_contacts.clear()

            flush_batch(db, batch, customer_map)
            for prof, cdata_list in pending_contacts:
                for cdata in cdata_list:
                    contact = Contact(
                        profile_id=prof.id,
                        name=cdata["name"],
                        job_title=cdata["job_title"],
                        email=cdata["email"],
                    )
                    db.add(contact)
                    db.flush()
                    if cdata["phone"]:
                        db.add(ContactPhone(contact_id=contact.id, type=PhoneType.work, number=cdata["phone"]))
                    if cdata["cell"]:
                        db.add(ContactPhone(contact_id=contact.id, type=PhoneType.mobile, number=cdata["cell"]))
            pending_contacts.clear()

            counts["customers"] = count

        # === 3. Vendors (tblVendors) ===
        if "tblVendors.csv" in file_contents:
            rows = parse_csv(file_contents["tblVendors.csv"])
            count = 0
            batch: list[tuple[int, Profile]] = []
            pending_contacts: list[tuple[Profile, dict]] = []

            for row in rows:
                legacy_id = safe_int(row.get("VendorID", ""))
                name = safe_str(row.get("chrCompanyName", ""))

                if not legacy_id:
                    warnings.append(f"Vendors: skipped row with empty ID")
                    continue

                if not name:
                    name = f"Unknown Vendor {legacy_id}"

                addr_parts = [
                    safe_str(row.get("chrAddress", "")),
                    safe_str(row.get("chrCity", "")),
                    safe_str(row.get("chrProvince", "")),
                ]
                address = ", ".join(p for p in addr_parts if p)

                profile = Profile(
                    name=name,
                    type=ProfileType.vendor,
                    pst=safe_str(row.get("chrProvincialTax", "")),
                    address=address,
                    postal_code=safe_str(row.get("chrPostalCode", "")),
                    legacy_source="tblVendors",   # Vision table this row came from
                    legacy_id=legacy_id,          # Vision VendorID
                )
                db.add(profile)
                batch.append((legacy_id, profile))
                count += 1

                # One contact per vendor
                first = safe_str(row.get("chrFirstName", ""))
                last = safe_str(row.get("chrLastName", ""))
                contact_name = f"{first} {last}".strip()
                if contact_name:
                    pending_contacts.append((profile, {
                        "name": contact_name,
                        "email": safe_str(row.get("chrEmailAddress", "")).strip("'") or None,
                        "phone": safe_str(row.get("chrPhoneNumber", "")),
                    }))

                if len(batch) >= BATCH_SIZE:
                    flush_batch(db, batch, vendor_map)
                    for prof, cdata in pending_contacts:
                        contact = Contact(
                            profile_id=prof.id,
                            name=cdata["name"],
                            email=cdata["email"],
                        )
                        db.add(contact)
                        db.flush()
                        if cdata["phone"]:
                            db.add(ContactPhone(contact_id=contact.id, type=PhoneType.work, number=cdata["phone"]))
                    pending_contacts.clear()

            flush_batch(db, batch, vendor_map)
            for prof, cdata in pending_contacts:
                contact = Contact(
                    profile_id=prof.id,
                    name=cdata["name"],
                    email=cdata["email"],
                )
                db.add(contact)
                db.flush()
                if cdata["phone"]:
                    db.add(ContactPhone(contact_id=contact.id, type=PhoneType.work, number=cdata["phone"]))
            pending_contacts.clear()

            counts["vendors"] = count

        # === 4. Parts (tblMaterial, skip LM- prefix) ===
        if "tblMaterial.csv" in file_contents:
            rows = parse_csv(file_contents["tblMaterial.csv"])
            count = 0
            skipped_lm = 0
            batch: list[tuple[int, Part]] = []

            for row in rows:
                legacy_id = safe_int(row.get("ProductID", ""))
                part_number = safe_str(row.get("chrProductName", ""))

                if not legacy_id or not part_number:
                    warnings.append(f"Parts: skipped row with empty ID or part_number")
                    continue

                # Skip LM- prefix rows (labor+material combos)
                if part_number.upper().startswith("LM-"):
                    skipped_lm += 1
                    continue

                cost = clean_currency(row.get("curNetPrice", ""))
                markup = safe_float(row.get("intMarkup", ""))
                vendor_legacy_id = safe_int(row.get("intVendor", ""))
                cat_legacy_id = safe_int(row.get("intCategory", ""))

                part = Part(
                    part_number=part_number,
                    description=safe_str(row.get("chrProductDescription", "")) or part_number,
                    cost=cost,
                    markup_percent=markup,
                    category_id=cat_map_part.get(cat_legacy_id),
                    vendor_id=vendor_map.get(vendor_legacy_id),
                    legacy_source="tblMaterial",  # Vision table this row came from
                    legacy_id=legacy_id,          # Vision ProductID
                )
                db.add(part)
                batch.append((legacy_id, part))
                count += 1

                if len(batch) >= BATCH_SIZE:
                    flush_batch(db, batch, part_map)

            flush_batch(db, batch, part_map)
            if skipped_lm:
                warnings.append(f"Parts: skipped {skipped_lm} LM- prefix rows")
            counts["parts"] = count

        # === 5. Labor (tblApplication) ===
        if "tblApplication.csv" in file_contents:
            rows = parse_csv(file_contents["tblApplication.csv"])
            count = 0
            batch: list[tuple[int, Labor]] = []

            for row in rows:
                legacy_id = safe_int(row.get("ProductID", ""))
                description = safe_str(row.get("chrProductDescription", ""))

                if not legacy_id or not description:
                    warnings.append(f"Labor: skipped row with empty ID or description")
                    continue

                hours_raw = safe_float(row.get("intTime", ""))
                net_price = clean_currency(row.get("curNetPrice", ""))
                markup = safe_float(row.get("intMarkup", ""))
                cat_legacy_id = safe_int(row.get("intCategory", ""))

                if hours_raw > 0:
                    rate = net_price / hours_raw
                    hours = hours_raw
                else:
                    rate = net_price
                    hours = 1

                labor_item = Labor(
                    description=description,
                    hours=hours,
                    rate=rate,
                    markup_percent=markup,
                    category_id=cat_map_labor.get(cat_legacy_id),
                    legacy_source="tblApplication",  # Vision table this row came from
                    legacy_id=legacy_id,             # Vision ProductID
                )
                db.add(labor_item)
                batch.append((legacy_id, labor_item))
                count += 1

                if len(batch) >= BATCH_SIZE:
                    flush_batch(db, batch, labor_map)

            flush_batch(db, batch, labor_map)
            counts["labor"] = count

        # === 6. Miscellaneous (tblZones) ===
        if "tblZones.csv" in file_contents:
            rows = parse_csv(file_contents["tblZones.csv"])
            count = 0
            batch: list[tuple[int, Miscellaneous]] = []

            for row in rows:
                legacy_id = safe_int(row.get("ZoneRateID", ""))

                if not legacy_id:
                    warnings.append(f"Miscellaneous: skipped row with empty ZoneRateID")
                    continue

                zone_name = safe_str(row.get("chrZones", ""))
                distance = safe_str(row.get("chrDistance", ""))
                if zone_name and distance:
                    desc = f"{zone_name} - {distance}"
                elif distance:
                    desc = distance
                elif zone_name:
                    desc = zone_name
                else:
                    desc = f"Zone {legacy_id}"

                unit_price = clean_currency(row.get("curNetPrice", ""))
                markup = safe_float(row.get("intMarkup", ""))

                misc = Miscellaneous(
                    description=desc,
                    unit_price=unit_price,
                    markup_percent=markup,
                    is_system_item=False,
                    legacy_source="tblZones",     # Vision table this row came from
                    legacy_id=legacy_id,          # Vision ZoneRateID
                )
                db.add(misc)
                batch.append((legacy_id, misc))
                count += 1

                if len(batch) >= BATCH_SIZE:
                    flush_batch(db, batch, misc_map)

            flush_batch(db, batch, misc_map)
            counts["miscellaneous"] = count

        # === 7. Projects (tblProjects) ===
        if "tblProjects.csv" in file_contents:
            rows = parse_csv(file_contents["tblProjects.csv"])
            count = 0
            batch: list[tuple[int, Project]] = []
            # Track UCA numbers to handle duplicates
            seen_uca: set[str] = set()
            placeholder_customer_id: Optional[int] = None  # created on first project with a missing customer

            for row in rows:
                legacy_id = safe_int(row.get("ProjectID", ""))
                name = safe_str(row.get("ProjectName", "")) or f"Project {legacy_id}"
                client_legacy_id = safe_int(row.get("ClientID", ""))

                if not legacy_id:
                    warnings.append(f"Projects: skipped row with empty ProjectID")
                    continue

                if client_legacy_id not in customer_map:
                    # Keep the project (and its work orders and POs) on a placeholder
                    # customer, like POs with a missing vendor; skipping it would drop
                    # open jobs Vision still has.
                    if placeholder_customer_id is None:
                        placeholder = Profile(name="Unknown Customer (Legacy)", type=ProfileType.customer, pst="", address="", postal_code="")
                        db.add(placeholder)
                        db.flush()                          # need its id now
                        placeholder_customer_id = placeholder.id
                    customer_map[client_legacy_id] = placeholder_customer_id  # every later row with this id reuses it
                    warnings.append(f"Projects: ProjectID {legacy_id} has unknown ClientID {client_legacy_id}; put on 'Unknown Customer (Legacy)'")

                # Legacy system stores numeric UCA, display format is "A" + 4-digit padded
                uca_raw = safe_str(row.get("UCAProjectNr", ""))
                if not uca_raw:
                    uca_raw = str(legacy_id)
                try:
                    uca_number = f"A{int(uca_raw):04d}"
                except ValueError:
                    uca_number = uca_raw  # Already has prefix or non-numeric

                # Handle duplicate UCA numbers
                if uca_number in seen_uca:
                    uca_number = f"{uca_number}-{legacy_id}"
                seen_uca.add(uca_number)

                created_on = parse_date(row.get("dtmStartDate", "")) or datetime.utcnow()
                # blnArchive is an Access boolean: TRUE exports as -1 (or 1). Use the
                # shared parser so a -1 export is read as archived, not active.
                status = "archived" if parse_bool(row.get("blnArchive")) else "active"

                project = Project(
                    name=name,
                    customer_id=customer_map[client_legacy_id],
                    created_on=created_on,
                    status=status,
                    ucsh_project_number=safe_str(row.get("UCSHProjectNr", "")) or None,
                    uca_project_number=uca_number,
                    project_lead=safe_str(row.get("EmployeeID", "")) or None,
                    legacy_source="tblProjects",  # Vision table this row came from
                    legacy_id=legacy_id,          # Vision ProjectID
                )
                db.add(project)
                batch.append((legacy_id, project))
                count += 1

                if len(batch) >= BATCH_SIZE:
                    flush_batch(db, batch, project_map)

            flush_batch(db, batch, project_map)
            counts["projects"] = count

        # === 8. Quotes (tblServiceRecords) ===
        # Status is NOT hardcoded (issue #164): every quote starts as a placeholder
        # "Draft" and is recomputed from its imported lines in step 11b.
        quote_objs: dict[int, Quote] = {}                  # legacy WorkorderID -> Quote, for the recompute
        closed_wos: set[int] = set()                       # WorkorderIDs Vision finished (all shipped or force closed)
        force_closed_wos: set[int] = set()                 # the force-closed subset, reported separately
        lines_by_wo: dict[int, list[QuoteLineItem]] = {}   # legacy WorkorderID -> its imported lines
        lines_without_ship_data = 0                        # lines that carried no ship fields at all
        if "tblServiceRecords.csv" in file_contents:
            rows = parse_csv(file_contents["tblServiceRecords.csv"])
            count = 0

            # Group by project for sequence assignment
            project_quotes: dict[int, list[dict]] = {}
            for row in rows:
                legacy_wo_id = safe_int(row.get("WorkorderID", ""))
                project_legacy_id = safe_int(row.get("PojectID", ""))

                if not legacy_wo_id:
                    warnings.append(f"Quotes: skipped row with empty WorkorderID")
                    continue

                if project_legacy_id not in project_map:
                    warnings.append(f"Quotes: skipped WorkorderID {legacy_wo_id} — unknown PojectID {project_legacy_id}")
                    continue

                row["_legacy_wo_id"] = str(legacy_wo_id)
                row["_project_legacy_id"] = str(project_legacy_id)
                project_quotes.setdefault(project_legacy_id, []).append(row)

            # Sort each group and assign sequences
            batch: list[tuple[int, Quote]] = []
            for proj_legacy_id, quote_rows in project_quotes.items():
                # Sort by date then by WorkorderID
                def sort_key(r):
                    dt = workorder_created(r) or datetime.min
                    return (dt, safe_int(r.get("WorkorderID", "")))

                quote_rows.sort(key=sort_key)

                for seq, row in enumerate(quote_rows, start=1):
                    legacy_wo_id = int(row["_legacy_wo_id"])

                    if workorder_closed(row):              # Vision's verdict: job finished
                        closed_wos.add(legacy_wo_id)        # lands Closed, its lines fully fulfilled
                    if workorder_force_closed(row):        # business override: job declared done
                        force_closed_wos.add(legacy_wo_id)  # counted in the response

                    # Issue #54: prepend the legacy UC Vision work-order number
                    # (WorkorderID) to the start of the work description so the old
                    # quote-number reference survives the migration.
                    work_description = wo_prefixed_description(
                        legacy_wo_id, row.get("memWorkDescription", "")
                    )

                    quote = Quote(
                        project_id=project_map[proj_legacy_id],
                        quote_sequence=seq,
                        created_at=workorder_created(row) or datetime.utcnow(),  # started, else entered
                        status="Draft",                     # placeholder; recomputed from lines (step 11b)
                        work_description=work_description,
                        client_po_number=safe_str(row.get("intPONumber", "")) or None,
                        cost_code_id=None,
                        current_version=0,
                        legacy_imported=True,               # migrated row: reports rely on it
                        legacy_source="tblServiceRecords",  # Vision table this row came from
                        legacy_id=legacy_wo_id,             # Vision WorkorderID
                    )
                    db.add(quote)
                    quote_objs[legacy_wo_id] = quote        # keep the object for the status recompute
                    batch.append((legacy_wo_id, quote))
                    count += 1

                    if len(batch) >= BATCH_SIZE:
                        flush_batch(db, batch, quote_map)

            flush_batch(db, batch, quote_map)
            counts["quotes"] = count

        # === 9. Quote Labor Items (tblWorkorderApplication) ===
        if "tblWorkorderApplication.csv" in file_contents:
            rows = parse_csv(file_contents["tblWorkorderApplication.csv"])
            count = 0
            for row in rows:
                wo_legacy_id = safe_int(row.get("intWorkorderID", ""))
                labor_legacy_id = safe_int(row.get("intProductName", ""))

                if wo_legacy_id not in quote_map:
                    warnings.append(f"Quote labor items: skipped row — unknown intWorkorderID {wo_legacy_id}")
                    continue

                quantity = line_quantity(row, "intQuantity")              # as Vision has it: fraction, 0 or negative kept
                fulfilled, pending, has_ship_data = line_close_state(   # real close-state (issue #164)
                    row, quantity, wo_legacy_id in closed_wos
                )
                if not has_ship_data:
                    lines_without_ship_data += 1                        # reported in the response

                item = QuoteLineItem(
                    quote_id=quote_map[wo_legacy_id],
                    item_type="labor",
                    labor_id=labor_map.get(labor_legacy_id),
                    description=safe_str(row.get("chrProductDescription", "")) or None,
                    quantity=quantity,
                    unit_price=clean_currency(row.get("curUnitPrice", "")),
                    base_cost=clean_currency(row.get("curNetPrice", "")),
                    qty_pending=pending,                                # remaining to invoice
                    qty_fulfilled=fulfilled,                            # shipped in Vision
                    legacy_source="tblWorkorderApplication",            # Vision table this row came from
                    legacy_id=opt_int(row.get("WorkorderPartID", "")),  # the line's own Vision key
                )
                db.add(item)
                lines_by_wo.setdefault(wo_legacy_id, []).append(item)  # for the status recompute
                count += 1

            counts["quote_labor_items"] = count

        # === 10. Quote Part Items (tblWorkorderMaterial) ===
        if "tblWorkorderMaterial.csv" in file_contents:
            rows = parse_csv(file_contents["tblWorkorderMaterial.csv"])
            count = 0
            for row in rows:
                wo_legacy_id = safe_int(row.get("intWorkorderID", ""))
                part_legacy_id = safe_int(row.get("intProductName", ""))

                if wo_legacy_id not in quote_map:
                    warnings.append(f"Quote part items: skipped row — unknown intWorkorderID {wo_legacy_id}")
                    continue

                quantity = line_quantity(row, "intQuantity")              # as Vision has it: fraction, 0 or negative kept
                fulfilled, pending, has_ship_data = line_close_state(   # real close-state (issue #164)
                    row, quantity, wo_legacy_id in closed_wos
                )
                if not has_ship_data:
                    lines_without_ship_data += 1                        # reported in the response

                item = QuoteLineItem(
                    quote_id=quote_map[wo_legacy_id],
                    item_type="part",
                    part_id=part_map.get(part_legacy_id),
                    description=safe_str(row.get("chrProductDescription", "")) or None,
                    quantity=quantity,
                    unit_price=clean_currency(row.get("curUnitPrice", "")),
                    base_cost=clean_currency(row.get("curNetPrice", "")),
                    qty_pending=pending,                                # remaining to invoice
                    qty_fulfilled=fulfilled,                            # shipped in Vision
                    legacy_source="tblWorkorderMaterial",               # Vision table this row came from
                    legacy_id=opt_int(row.get("WorkorderPartID", "")),  # the line's own Vision key
                )
                db.add(item)
                lines_by_wo.setdefault(wo_legacy_id, []).append(item)  # for the status recompute
                count += 1

            counts["quote_part_items"] = count

        # === 11. Quote Misc Items (tblWorkorderZones) ===
        if "tblWorkorderZones.csv" in file_contents:
            rows = parse_csv(file_contents["tblWorkorderZones.csv"])
            count = 0
            for row in rows:
                wo_legacy_id = safe_int(row.get("intWorkorderID", ""))
                zone_legacy_id = safe_int(row.get("chrZones", ""))

                if wo_legacy_id not in quote_map:
                    warnings.append(f"Quote misc items: skipped row — unknown intWorkorderID {wo_legacy_id}")
                    continue

                quantity = line_quantity(row, "intQuantity")              # as Vision has it: fraction, 0 or negative kept
                fulfilled, pending, has_ship_data = line_close_state(   # real close-state (issue #164)
                    row, quantity, wo_legacy_id in closed_wos
                )
                if not has_ship_data:
                    lines_without_ship_data += 1                        # reported in the response

                item = QuoteLineItem(
                    quote_id=quote_map[wo_legacy_id],
                    item_type="misc",
                    misc_id=misc_map.get(zone_legacy_id),
                    description=safe_str(row.get("chrDistance", "")) or None,
                    quantity=quantity,
                    unit_price=clean_currency(row.get("curPrice", "")),
                    base_cost=clean_currency(row.get("curNetPrice", "")),
                    qty_pending=pending,                                # remaining to invoice
                    qty_fulfilled=fulfilled,                            # shipped in Vision
                    legacy_source="tblWorkorderZones",                  # Vision table this row came from
                    legacy_id=opt_int(row.get("WorkorderPartID", "")),  # the line's own Vision key
                )
                db.add(item)
                lines_by_wo.setdefault(wo_legacy_id, []).append(item)  # for the status recompute
                count += 1

            counts["quote_misc_items"] = count

        # === 11b. Quote close-state (issue #164) ===
        # A work order Vision finished is Closed, whatever its lines say (the app's
        # rule cannot produce Closed for a job with no lines, or only zero/credit
        # lines). Every other quote gets the app's own rule over its real line
        # fulfilment, so the import and the editor can never disagree.
        quotes_open = 0
        quotes_closed = 0
        closed_over_lines = 0                                           # finished in Vision, lines alone say open
        for wo_legacy_id, quote in quote_objs.items():
            lines = lines_by_wo.get(wo_legacy_id, [])
            from_lines = compute_status_from_lines(lines, quote.client_po_number)  # Closed / Invoiced / Work Order / Draft
            if wo_legacy_id in closed_wos:                              # Vision's verdict wins
                quote.status = "Closed"
                if not lines or not any(li.qty_fulfilled > 0 for li in lines):
                    closed_over_lines += 1                              # nothing shipped on record, still closed
            elif from_lines in ("Draft", "Work Order") and any(li.qty_fulfilled < 0 for li in lines):
                # Only credit lines were processed in Vision. The app's rule counts
                # positive fulfilment only, so decide here: nothing left -> Closed,
                # positive lines still pending -> Invoiced.
                quote.status = "Closed" if all(li.qty_pending == 0 for li in lines) else "Invoiced"
            else:
                quote.status = from_lines
            if quote.status == "Closed":
                quotes_closed += 1
            else:
                quotes_open += 1
        if quote_objs:                                                  # report the split to the operator
            counts["quotes_closed"] = quotes_closed
            counts["quotes_open"] = quotes_open
            counts["quotes_force_closed"] = len(force_closed_wos)
            counts["quote_lines_without_ship_data"] = lines_without_ship_data
            if lines_without_ship_data:
                warnings.append(
                    f"Quote lines: {lines_without_ship_data} carried no ship quantities "
                    "and were imported as fully pending"
                )
            if closed_over_lines:
                warnings.append(
                    f"Quotes: {closed_over_lines} finished in Vision with nothing shipped on record "
                    "(no lines, or only zero/credit lines) were imported Closed"
                )

        # === 12. Purchase Orders (tblPurchaseOrders) ===
        # Status is NOT hardcoded closed: each PO starts as a placeholder Draft and
        # is recomputed from its line receipts in step 13b.
        po_objs: dict[int, PurchaseOrder] = {}             # legacy PurchaseOrderID -> PO, for the recompute
        po_lines_by_po: dict[int, list[POLineItem]] = {}   # legacy PurchaseOrderID -> its imported lines
        po_received_all: dict[int, Optional[bool]] = {}    # Vision's own "received all" flag; wins over receipts
        received_short_by_po: dict[int, bool] = {}         # flagged received-all but receipts fell short
        fractional_po_lines: list[str] = []                # PO lines whose fractional quantity was rounded
        if "tblPurchaseOrders.csv" in file_contents:
            rows = parse_csv(file_contents["tblPurchaseOrders.csv"])
            count = 0

            # Create placeholder vendor for POs with missing vendor references
            placeholder_vendor = Profile(name="Unknown Vendor (Legacy)", type=ProfileType.vendor, pst="", address="", postal_code="")
            db.add(placeholder_vendor)
            db.flush()
            placeholder_vendor_id = placeholder_vendor.id

            # Group by project for sequence assignment
            project_pos: dict[int, list[dict]] = {}
            for row in rows:
                legacy_po_id = safe_int(row.get("PurchaseOrderID", ""))
                proj_legacy_id = safe_int(row.get("intProjectID", ""))

                if not legacy_po_id:
                    warnings.append(f"POs: skipped row with empty PurchaseOrderID")
                    continue

                if proj_legacy_id not in project_map:
                    warnings.append(f"POs: skipped POID {legacy_po_id} — unknown intProjectID {proj_legacy_id}")
                    continue

                vendor_legacy_id = safe_int(row.get("intVendorID", ""))
                if vendor_legacy_id not in vendor_map:
                    # Use placeholder instead of skipping
                    if 0 not in vendor_map:
                        vendor_map[0] = placeholder_vendor_id
                    vendor_legacy_id = 0

                row["_legacy_po_id"] = str(legacy_po_id)
                row["_proj_legacy_id"] = str(proj_legacy_id)
                row["_vendor_legacy_id"] = str(vendor_legacy_id)
                project_pos.setdefault(proj_legacy_id, []).append(row)

            batch: list[tuple[int, PurchaseOrder]] = []
            for proj_legacy_id, po_rows in project_pos.items():
                def sort_key(r):
                    dt = parse_date(r.get("dtmOrderDate", "")) or datetime.min
                    return (dt, safe_int(r.get("PurchaseOrderID", "")))

                po_rows.sort(key=sort_key)

                for seq, row in enumerate(po_rows, start=1):
                    legacy_po_id = int(row["_legacy_po_id"])
                    vendor_legacy_id = int(row["_vendor_legacy_id"])

                    po = PurchaseOrder(
                        project_id=project_map[proj_legacy_id],
                        vendor_id=vendor_map[vendor_legacy_id],
                        po_sequence=seq,
                        created_at=parse_date(row.get("dtmOrderDate", "")) or datetime.utcnow(),
                        status=POStatus.draft,              # placeholder; recomputed from receipts (step 13b)
                        work_description=safe_str(row.get("memNote", "")) or None,
                        cost_code_id=None,
                        current_version=0,
                        legacy_imported=True,               # migrated row
                        legacy_source="tblPurchaseOrders",  # Vision table this row came from
                        legacy_id=legacy_po_id,             # Vision PurchaseOrderID
                    )
                    db.add(po)
                    po_objs[legacy_po_id] = po              # keep the object for the status recompute
                    po_received_all[legacy_po_id] = parse_bool(row.get("blnRecievedAll", ""))  # sic: Vision's spelling
                    batch.append((legacy_po_id, po))
                    count += 1

                    if len(batch) >= BATCH_SIZE:
                        flush_batch(db, batch, po_map)

            flush_batch(db, batch, po_map)
            counts["purchase_orders"] = count

        # === 13. PO Line Items (tblPurchaseOrdersMaterial) ===
        if "tblPurchaseOrdersMaterial.csv" in file_contents:
            rows = parse_csv(file_contents["tblPurchaseOrdersMaterial.csv"])
            count = 0
            for row in rows:
                po_legacy_id = safe_int(row.get("intPurchaseOrderID", ""))
                part_legacy_id = safe_int(row.get("intProductID", ""))

                if po_legacy_id not in po_map:
                    warnings.append(f"PO line items: skipped row — unknown intPurchaseOrderID {po_legacy_id}")
                    continue

                vision_qty = line_quantity(row, "intQtyOrdered")             # as Vision has it, may be fractional
                quantity = whole_po_quantity(vision_qty)                     # PO lines are whole numbers in Velocity
                if quantity != vision_qty:
                    fractional_po_lines.append(                              # reported, never silently changed
                        f"{opt_int(row.get('PurchaseOrderPartID', ''))} ({vision_qty:g} -> {quantity})"
                    )
                received = sum(                                              # the three Vision receipt slots
                    opt_qty(row.get(f)) or 0.0
                    for f in ("intQtyReceived1", "intQtyReceived2", "intQtyReceived3")
                )
                qty_received = whole_po_quantity(received)                  # same rounding as the quantity
                if quantity <= 0:                                            # zero / credit line: nothing to receive
                    qty_pending = 0
                elif po_received_all.get(po_legacy_id) is True:             # Vision says received all
                    if qty_received < quantity:
                        received_short_by_po[po_legacy_id] = True            # reported in 13b
                    qty_received = max(qty_received, quantity)               # the line is done
                    qty_pending = 0
                else:
                    qty_pending = max(0, quantity - qty_received)

                item = POLineItem(
                    purchase_order_id=po_map[po_legacy_id],
                    item_type="part",
                    part_id=part_map.get(part_legacy_id),
                    description=safe_str(row.get("chrProductDescription", "")) or None,
                    quantity=quantity,
                    unit_price=clean_currency(row.get("curUnitPrice", "")),
                    qty_received=qty_received,
                    qty_pending=qty_pending,
                    legacy_source="tblPurchaseOrdersMaterial",              # Vision table this row came from
                    legacy_id=opt_int(row.get("PurchaseOrderPartID", "")),  # the line's own Vision key
                )
                db.add(item)
                po_lines_by_po.setdefault(po_legacy_id, []).append(item)  # for the status recompute
                count += 1

            counts["po_line_items"] = count

        # === 13b. Purchase-order close-state ===
        # Same idea as 11b: Vision's received-all flag lands a PO Received (its lines
        # were already marked done in step 13); otherwise the app's own rule over the
        # line receipts decides (no receipts -> Draft, all received -> Received,
        # else Sent). Zero and credit lines are left out of the rule: they have
        # nothing to receive, so they must not make an untouched PO look partial.
        po_status_counts = {"draft": 0, "sent": 0, "received": 0}
        received_over_lines = 0                                # flag set, receipts on record fell short
        for legacy_po_id, po in po_objs.items():
            lines = po_lines_by_po.get(legacy_po_id, [])
            positive = [li for li in lines if li.quantity > 0]  # only lines with something to receive
            if po_received_all.get(legacy_po_id) is True:      # Vision's verdict wins
                po.status = POStatus.received
                if received_short_by_po.get(legacy_po_id):
                    received_over_lines += 1
            else:
                po.status = compute_po_status(positive)
            po_status_counts[po.status.name] += 1
        if po_objs:                                            # report the split to the operator
            counts["purchase_orders_draft"] = po_status_counts["draft"]
            counts["purchase_orders_sent"] = po_status_counts["sent"]
            counts["purchase_orders_received"] = po_status_counts["received"]
            if received_over_lines:
                warnings.append(
                    f"Purchase orders: {received_over_lines} marked received-all in Vision with "
                    "short line receipts were imported Received, their lines marked received"
                )
        if fractional_po_lines:
            warnings.append(
                "PO lines: fractional quantities rounded to whole numbers "
                f"(PurchaseOrderPartID: {', '.join(fractional_po_lines)})"
            )

        # Commit the entire import
        db.commit()

        return {
            "success": True,
            "counts": counts,
            "warnings": warnings,
            "errors": errors,
            "skipped_files": skipped_files,
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Migration failed, all changes rolled back: {str(e)}",
        )
