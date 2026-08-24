"""Vision -> Velocity transform (compute side, quote domain).

Pure, DB-free functions that map rows from the staged ``vision_legacy`` schema
(see ``stage_raw.py``) into Velocity-shaped dictionaries. This is the *read /
compute* half of the migration: it never writes to any application table and
never opens a database session, so it is trivially unit-testable and cannot
touch production.

It intentionally MIRRORS the field mapping in ``backend/routes/migration.py``
(the original CSV importer) so parity is auditable, with the deliberate
correction the parity register flagged as G1 (``docs/MIGRATION_PARITY.md``):

    G1 -- Quote close-state was hardcoded. The importer set every quote to
    ``status="Closed"`` and every line to ``qty_fulfilled=quantity`` /
    ``qty_pending=0``, so the whole migrated back-catalogue reports Closed even
    though the real Vision ship quantities show ~43% of workorders were still
    open. Here we read the real per-line ship fields
    (``intTotalShippedQuantity`` / ``intShipQuantity`` / ``intQuantityBO``) and
    derive ``qty_fulfilled`` / ``qty_pending`` from them. We do NOT set the
    quote status ourselves -- the app's ``compute_quote_status`` derives it from
    those line quantities at write time. Getting the quantities right is what
    makes the status right.

Pricing is carried verbatim (``curUnitPrice`` / ``curNetPrice`` /
``curPrice``), exactly as the importer does. Markup / base-cost math stays the
application's job (``calculate_base_cost`` in ``routes/quotes.py``) at write
time and is never re-implemented here -- the pricing math is off-limits.

Values coming out of ``vision_legacy`` are already typed (``stage_raw`` coerces
Access types to Postgres types, so currency -> ``Decimal``, dates ->
``datetime``, Access booleans -> ``bool``), but every helper below also tolerates
raw strings so the same functions can be unit-tested with plain dict fixtures
and reused against a CSV source if needed.

This module is stdlib-only on purpose: it imports nothing from this package or
from the FastAPI backend, so it can be imported (and its tests collected) on
Linux CI where ``pywin32`` / ``psycopg2`` are not installed.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Optional


# --------------------------------------------------------------------------- #
# Coercion helpers -- pure, and tolerant of typed-or-string inputs.
# --------------------------------------------------------------------------- #

def to_str(val: Any, default: str = "") -> str:
    """Stripped string, or ``default`` for None/blank."""
    if val is None:
        return default
    s = str(val).strip()
    return s or default


def to_int(val: Any, default: int = 0) -> int:
    """Parse to int. Blank/None -> ``default``. Floats/Decimals are rounded.

    ``bool`` is handled explicitly because it is a subclass of ``int`` and we
    want ``True`` -> 1 rather than a surprise elsewhere.
    """
    if val is None:
        return default
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, (float, Decimal)):
        return int(round(float(val)))
    s = str(val).strip()
    if not s:
        return default
    try:
        return int(round(float(s)))
    except (ValueError, TypeError):
        return default


def opt_int(val: Any) -> Optional[int]:
    """Like :func:`to_int` but returns None (not 0) when the value is
    absent/blank/unparseable. Used where "no value recorded" must be
    distinguished from a real zero (e.g. ship quantities, legacy foreign keys).
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, (float, Decimal)):
        return int(round(float(val)))
    s = str(val).strip()
    if not s:
        return None
    try:
        return int(round(float(s)))
    except (ValueError, TypeError):
        return None


def to_float(val: Any, default: float = 0.0) -> float:
    """Parse to float, handling ``Decimal`` (staged NUMERIC), currency symbols,
    and accounting-style negatives ``(722.68)`` -> ``-722.68`` -- mirroring the
    importer's ``clean_currency``.
    """
    if val is None:
        return default
    if isinstance(val, bool):
        return float(val)
    if isinstance(val, (int, float, Decimal)):
        return float(val)
    s = str(val).strip()
    if not s:
        return default
    s = s.replace("$", "").replace(",", "").strip()
    if not s:
        return default
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def to_bool(val: Any) -> Optional[bool]:
    """Parse an Access boolean to True/False, or None if indeterminate.

    Access stores TRUE as -1; staging maps it to a Postgres BOOLEAN, but we also
    accept the raw ``-1`` / ``"-1"`` / ``"true"`` forms for fixtures.
    """
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float, Decimal)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("1", "-1", "true", "yes", "y", "t"):
        return True
    if s in ("0", "false", "no", "n", "f"):
        return False
    return None


_DATE_FORMATS = (
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def to_datetime(val: Any) -> Optional[datetime]:
    """Pass a ``datetime`` through; parse Access date strings; else None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def wo_prefixed_description(legacy_wo_id: int, raw_desc: Any) -> str:
    """Prepend the legacy UC Vision work-order number as ``[WO {id}]``.

    Mirrors ``routes.migration.wo_prefixed_description`` (Issue #54): in Vision
    the WorkorderID was the human-facing quote number, so it is preserved at the
    start of the migrated work description. A blank description collapses to just
    the tag.
    """
    prefix = f"[WO {legacy_wo_id}]"
    desc = to_str(raw_desc)
    return f"{prefix} {desc}" if desc else prefix


# --------------------------------------------------------------------------- #
# Quote domain transform
# --------------------------------------------------------------------------- #

# ``item_type`` -> where that line type reads its fields FROM in the staged
# workorder tables. labour = tblWorkorderApplication, part =
# tblWorkorderMaterial, misc = tblWorkorderZones. The three tables share the
# same shape except for the reference key, description column, and price column
# -- matching how routes/migration.py reads them.
_LINE_SPECS: dict[str, dict[str, str]] = {
    "labor": {
        "ref_key": "intProductName",
        "ref_field": "labor_legacy_id",
        "desc_key": "chrProductDescription",
        "price_key": "curUnitPrice",
    },
    "part": {
        "ref_key": "intProductName",
        "ref_field": "part_legacy_id",
        "desc_key": "chrProductDescription",
        "price_key": "curUnitPrice",
    },
    "misc": {
        "ref_key": "chrZones",
        "ref_field": "misc_legacy_id",
        "desc_key": "chrDistance",
        "price_key": "curPrice",
    },
}

# The source's own close-state labels (chrStatus text). Normalized lower-case.
_CLOSED_STATUS_TOKENS = frozenset({"closed", "complete", "completed", "invoiced", "done"})
_OPEN_STATUS_TOKENS = frozenset({"open", "active", "wip", "in progress", "pending"})


def derive_line_fulfillment(row: dict[str, Any], quantity: int) -> Optional[tuple[int, int]]:
    """Derive ``(qty_fulfilled, qty_pending)`` from the real Vision ship fields.

    This is the heart of the G1 fix. Priority of signals:
      1. ``intTotalShippedQuantity`` -- the running total shipped for the line.
      2. ``intShipQuantity`` -- a single-shipment quantity, if the total is absent.
      3. ``intQuantityBO`` (back-order) -- infer shipped = quantity - backordered.

    Returns None if the row carries NONE of those fields (so the caller can tell
    "no ship data recorded" apart from "zero shipped"). The result is clamped to
    ``[0, quantity]`` so bad legacy data can't produce negative or over-fulfilled
    quantities.
    """
    shipped = opt_int(row.get("intTotalShippedQuantity"))
    if shipped is None:
        shipped = opt_int(row.get("intShipQuantity"))
    if shipped is None:
        backordered = opt_int(row.get("intQuantityBO"))
        if backordered is not None:
            shipped = quantity - backordered
    if shipped is None:
        return None
    shipped = max(0, min(shipped, quantity))
    return shipped, quantity - shipped


def transform_workorder_line(row: dict[str, Any], item_type: str, force_closed: bool = False) -> dict[str, Any]:
    """Map one staged workorder-line row to a Velocity quote line-item dict.

    ``item_type`` is one of ``"labor"`` / ``"part"`` / ``"misc"``. The returned
    dict carries the *legacy* reference id (resolved to a Velocity id only at
    write time) and the real fulfillment split. When the row has no ship data at
    all, we default to fully PENDING and flag ``ship_data_present=False`` -- the
    conservative choice (don't claim work was done when Vision didn't record it),
    surfaced so the reconciler can report how much of the catalogue is uncertain.

    Args:
        row: One staged workorder-line row.
        item_type: ``"labor"`` / ``"part"`` / ``"misc"`` -- selects the field spec.
        force_closed: True when the OWNING workorder header was manually force-closed
            (see :func:`workorder_force_closed`). Then the line is treated as fully
            fulfilled (``qty_fulfilled = quantity``, ``qty_pending = 0``) regardless of
            ship quantities -- the business considers such jobs done, and without this
            the re-import would wrongly reopen them. Close-state ONLY.

    Returns:
        A quote line-item dict with the legacy ref id, ordered quantity, pricing
        fields (unchanged), and the fulfillment split.
    """
    if item_type not in _LINE_SPECS:
        raise ValueError(f"unknown item_type {item_type!r}")
    spec = _LINE_SPECS[item_type]

    quantity = max(1, to_int(row.get("intQuantity"), default=1))
    if force_closed:
        # Owning workorder force-closed -> job done by business decision: the whole
        # line is fulfilled, nothing pending, regardless of Vision ship quantities.
        # Close-state ONLY -- the ordered quantity, unit_price and base_cost below are
        # left exactly as-is (no pricing/math is touched).
        qty_fulfilled, qty_pending, ship_present = quantity, 0, True
    else:
        fulfillment = derive_line_fulfillment(row, quantity)   # normal per-line ship derivation
        if fulfillment is None:
            qty_fulfilled, qty_pending, ship_present = 0, quantity, False
        else:
            qty_fulfilled, qty_pending = fulfillment
            ship_present = True

    return {
        # The line's OWN Vision primary key (WorkorderPartID on each of the three
        # workorder-line tables) -- the stable legacy_id the write path keys on so a
        # re-run UPDATEs the same line instead of duplicating it.
        "line_legacy_id": opt_int(row.get("WorkorderPartID")),
        "workorder_legacy_id": opt_int(row.get("intWorkorderID")),
        "item_type": item_type,
        spec["ref_field"]: opt_int(row.get(spec["ref_key"])),
        "description": to_str(row.get(spec["desc_key"])) or None,
        "quantity": quantity,
        "unit_price": to_float(row.get(spec["price_key"])),
        "base_cost": to_float(row.get("curNetPrice")),
        "qty_fulfilled": qty_fulfilled,
        "qty_pending": qty_pending,
        "ship_data_present": ship_present,
    }


def workorder_source_closed(row: dict[str, Any]) -> Optional[bool]:
    """The workorder's OWN declared close-state, from ``chrStatus`` +
    ``blnForceClosed`` / ``blnAllShipped``.

    Returns True/False, or None when the source declares nothing usable. This is
    the source's *self-reported* label -- distinct from the state derived from
    line ship quantities. The reconciler reports both and their disagreement, a
    useful data-quality signal; the write-side status ultimately comes from the
    quantities via ``compute_quote_status``.
    """
    if to_bool(row.get("blnForceClosed")) is True:
        return True
    if to_bool(row.get("blnAllShipped")) is True:
        return True
    status = to_str(row.get("chrStatus")).lower()
    if status in _CLOSED_STATUS_TOKENS:
        return True
    if status in _OPEN_STATUS_TOKENS:
        return False
    return None


def workorder_force_closed(row: dict[str, Any]) -> bool:
    """Whether a workorder header was manually FORCE-CLOSED.

    Force-close is a business override: staff mark a job done even though not every
    line fully shipped, so it must TRUMP the per-line ship-quantity derivation --
    otherwise a re-import would wrongly reopen these finished jobs (~249 of them). It
    is signalled two ways on ``tblServiceRecords``, EITHER of which counts:
      * ``blnForceClosed`` is true, or
      * ``chrStatus`` begins with "Force Closed" (case-insensitive; the source has
        free-text variants like "Force Closed by DPowell.").

    Args:
        row: A staged ``tblServiceRecords`` (workorder header) row.

    Returns:
        True if the header is force-closed, else False.
    """
    if to_bool(row.get("blnForceClosed")) is True:        # explicit force-close flag
        return True
    status = to_str(row.get("chrStatus")).strip().lower() # normalize the free-text status
    return status.startswith("force closed")              # catch "Force Closed by X." variants


def transform_workorder(row: dict[str, Any]) -> dict[str, Any]:
    """Map one staged ``tblServiceRecords`` row to a Velocity quote header dict.

    Status is intentionally NOT set: ``compute_quote_status`` derives it from the
    line fulfillment quantities at write time (the G1 fix). The source's own
    close-state labels are carried through as ``legacy_*`` fields for reporting
    and as a searchable legacy reference. ``project_legacy_id`` reads the Vision
    column ``PojectID`` -- the misspelling is in the source and is preserved.
    """
    legacy_wo_id = to_int(row.get("WorkorderID"))
    return {
        "workorder_legacy_id": legacy_wo_id,
        "project_legacy_id": opt_int(row.get("PojectID")),
        "created_at": to_datetime(row.get("dtmDateStarted")),
        "work_description": wo_prefixed_description(legacy_wo_id, row.get("memWorkDescription")),
        "quote_description": to_str(row.get("memQuoteDescription")) or None,
        "client_po_number": to_str(row.get("intPONumber")) or None,
        "legacy_status": to_str(row.get("chrStatus")) or None,
        "legacy_force_closed": to_bool(row.get("blnForceClosed")),
        "legacy_all_shipped": to_bool(row.get("blnAllShipped")),
    }


# --------------------------------------------------------------------------- #
# Invoice domain (reconstructed from the three ...Shipped ledgers)
# --------------------------------------------------------------------------- #
# Vision has no invoice table: an invoice is the set of shipment-event rows in the
# tblWorkorder{Application,Material,Zones}Shipped ledgers that share one
# (intWorkorderID, intInvoiceNumber). intInvoiceNumber is GLOBALLY UNIQUE across all
# three ledgers and workorders (verified), so it is the invoice's stable legacy_id.
# Rows with intInvoiceNumber == 0 are shipped-but-UNBILLED and are excluded upstream
# (they never form an invoice); their quantity already lives in the quote line's
# fulfillment split from the line import.

def transform_invoice(workorder_id: int, invoice_number: int,
                      events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconstruct one Velocity invoice header from a group of shipment events.

    Args:
        workorder_id: The Vision workorder id shared by the group (``intWorkorderID``)
            -- resolves to the parent quote at write time.
        invoice_number: The Vision invoice number shared by the group
            (``intInvoiceNumber``); globally unique, so it is the invoice legacy_id.
        events: The ``...Shipped`` rows in this invoice (across the three ledgers).

    Returns:
        An invoice header dict: the legacy id, the parent workorder id, the invoice
        DATE (earliest ship date in the group), and Vision's own ``chrFullInvoiceNumber``
        (e.g. "6-78-0") preserved for traceability (Velocity computes its own number).
    """
    dates = [d for d in (to_datetime(e.get("dtmShipDate")) for e in events) if d is not None]
    # Vision's own display number, kept for the migrated invoice's notes (first non-blank).
    full = next((to_str(e.get("chrFullInvoiceNumber")) for e in events
                 if to_str(e.get("chrFullInvoiceNumber"))), None)
    return {
        "invoice_legacy_id": invoice_number,     # globally-unique Vision invoice number = stable key
        "workorder_legacy_id": workorder_id,     # -> the parent quote
        "invoice_date": min(dates) if dates else None,   # invoice raised at the earliest shipment
        "full_invoice_number": full,             # Vision's "{inv}-{wo}-{ext}" display, kept in notes
    }


def transform_invoice_line(row: dict[str, Any], item_type: str) -> dict[str, Any]:
    """Map one Vision shipment-event row to a Velocity invoice line-item dict.

    Args:
        row: One staged ``...Shipped`` row (one shipment/billing event).
        item_type: ``"part"`` / ``"labor"`` / ``"misc"`` -- which ledger it came from,
            selecting the Velocity ``item_type`` and which catalog map resolves the ref.

    Returns:
        An invoice line dict: this event's own key (``WorkorderPartID``), the parent
        invoice's key (``intInvoiceNumber``), the migrated quote LINE's key
        (``intWorkorderPartID`` = that line's WorkorderPartID), the catalog ref, unit
        price, and the quantity BILLED on this invoice (``intShipQuantity``) plus the
        line's ordered quantity.
    """
    return {
        "event_legacy_id": opt_int(row.get("WorkorderPartID")),      # this shipment event's PK
        "invoice_legacy_id": opt_int(row.get("intInvoiceNumber")),   # -> parent invoice
        "line_legacy_id": opt_int(row.get("intWorkorderPartID")),    # -> the migrated quote line
        "item_type": item_type,
        "product_legacy_id": opt_int(row.get("intProductID")),       # catalog ref (part/labour/misc)
        "unit_price": to_float(row.get("curUnitPrice")),             # priced as Vision recorded it
        # Quantity billed on THIS invoice. KNOWN PARITY GAP: Velocity invoice quantities are
        # INTEGERS (app-wide since migration 003), so a fractional Vision ship quantity is
        # ROUNDED here. ~603 of ~53k billed events are fractional -> the reconstructed invoice
        # totals differ from Vision by ~0.05% ($37.8k of $75.1M). Accepted by decision: it is
        # inherent to the integer-quantity model and consistent with the quote lines (also
        # integer), and unit prices are kept accurate rather than distorted to force the total.
        "ship_qty": to_int(row.get("intShipQuantity"), default=0),
        "ordered_qty": to_int(row.get("intQuantity"), default=0),    # the line's ordered quantity
    }


# --------------------------------------------------------------------------- #
# Catalog domain (categories / parts / labour / misc)
# --------------------------------------------------------------------------- #

def transform_category(row: dict[str, Any]) -> list[dict[str, Any]]:
    """tblPartsCategories -> Category(s). ``chrCategoryType`` selects the
    Velocity catalog type: Application -> labor, Material -> part, and
    "Application & Material" -> BOTH (the importer creates two Category rows).
    Returns a list (0, 1, or 2 dicts) so the both-case is faithful.
    """
    legacy_id = opt_int(row.get("CategoryID"))
    name = to_str(row.get("chrCategoryName")) or None
    ctype = to_str(row.get("chrCategoryType"))
    if not legacy_id or not name:
        return []
    if ctype == "Application":
        return [{"category_legacy_id": legacy_id, "name": name, "type": "labor"}]
    if ctype == "Material":
        return [{"category_legacy_id": legacy_id, "name": name, "type": "part"}]
    if ctype == "Application & Material":
        return [
            {"category_legacy_id": legacy_id, "name": name, "type": "part"},
            {"category_legacy_id": legacy_id, "name": name, "type": "labor"},
        ]
    return []  # unknown type -> importer skips


def transform_part(row: dict[str, Any]) -> dict[str, Any]:
    """tblMaterial -> Part. ``skipped_lm`` flags an ``LM-`` combo part, which the
    importer drops from the catalog (G7) -- workorder lines referencing it then
    import detached (null part_id). Pricing (cost/markup) carried verbatim.
    """
    part_number = to_str(row.get("chrProductName")) or None
    is_lm = bool(part_number and part_number.upper().startswith("LM-"))
    return {
        "part_legacy_id": opt_int(row.get("ProductID")),
        "part_number": part_number,
        "description": to_str(row.get("chrProductDescription")) or part_number,
        "cost": to_float(row.get("curNetPrice")),
        "markup_percent": to_float(row.get("intMarkup")),
        "vendor_legacy_id": opt_int(row.get("intVendor")),
        "category_legacy_id": opt_int(row.get("intCategory")),
        "skipped_lm": is_lm,
    }


def transform_labor(row: dict[str, Any]) -> dict[str, Any]:
    """tblApplication -> Labor. Rate is derived as net_price / hours when hours
    are present (else the net price is treated as the rate for a 1-hour unit),
    mirroring the importer."""
    hours_raw = to_float(row.get("intTime"))
    net_price = to_float(row.get("curNetPrice"))
    if hours_raw > 0:
        rate, hours = net_price / hours_raw, hours_raw
    else:
        rate, hours = net_price, 1.0
    return {
        "labor_legacy_id": opt_int(row.get("ProductID")),
        "description": to_str(row.get("chrProductDescription")) or None,
        "hours": hours,
        "rate": rate,
        "markup_percent": to_float(row.get("intMarkup")),
        "category_legacy_id": opt_int(row.get("intCategory")),
    }


def transform_misc(row: dict[str, Any]) -> dict[str, Any]:
    """tblZones -> Miscellaneous. Description is assembled from zone + distance."""
    legacy_id = opt_int(row.get("ZoneRateID"))
    zone = to_str(row.get("chrZones"))
    distance = to_str(row.get("chrDistance"))
    if zone and distance:
        desc = f"{zone} - {distance}"
    elif distance:
        desc = distance
    elif zone:
        desc = zone
    else:
        desc = f"Zone {legacy_id}"
    return {
        "misc_legacy_id": legacy_id,
        "description": desc,
        "unit_price": to_float(row.get("curNetPrice")),
        "markup_percent": to_float(row.get("intMarkup")),
        "is_system_item": False,
    }


# --------------------------------------------------------------------------- #
# Profiles (customers / vendors) + contacts
# --------------------------------------------------------------------------- #

def _contact(name: str, title: Any, email: Any, phone: Any, cell: Any) -> dict[str, Any]:
    return {
        "name": name,
        "job_title": to_str(title) or None,
        "email": to_str(email) or None,
        "phone": to_str(phone) or None,   # -> ContactPhone(work)
        "cell": to_str(cell) or None,     # -> ContactPhone(mobile)
    }


def transform_profile(row: dict[str, Any], profile_type: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """tblClients ('customer') / tblVendors ('vendor') -> (profile_dict, contacts).

    Mirrors the importer: PST from chrProvincialTax, address assembled from
    address/city/province, blank company name -> "Unknown {Type} {id}". Customers
    can carry a second contact; vendors carry one.
    """
    if profile_type == "customer":
        legacy_id = opt_int(row.get("Client ID"))
        id_field = "customer_legacy_id"
        fallback = f"Unknown Customer {legacy_id}"
    elif profile_type == "vendor":
        legacy_id = opt_int(row.get("VendorID"))
        id_field = "vendor_legacy_id"
        fallback = f"Unknown Vendor {legacy_id}"
    else:
        raise ValueError(f"unknown profile_type {profile_type!r}")

    name = to_str(row.get("chrCompanyName")) or fallback
    address = ", ".join(p for p in (
        to_str(row.get("chrAddress")), to_str(row.get("chrCity")), to_str(row.get("chrProvince")),
    ) if p)
    profile = {
        id_field: legacy_id,
        "type": profile_type,
        "name": name,
        "pst": to_str(row.get("chrProvincialTax")),
        "address": address,
        "postal_code": to_str(row.get("chrPostalCode")),
    }

    contacts: list[dict[str, Any]] = []
    name1 = f"{to_str(row.get('chrFirstName'))} {to_str(row.get('chrLastName'))}".strip()
    if name1:
        if profile_type == "customer":
            contacts.append(_contact(name1, row.get("chrTitle"), row.get("chrEmailAddress"),
                                     row.get("chrPhoneNumber"), row.get("chrCell")))
        else:
            # Vendors get a REDUCED contact, mirroring the importer: name +
            # quote-stripped email + work phone only (no job_title, no mobile).
            contacts.append({
                "name": name1,
                "job_title": None,
                "email": to_str(row.get("chrEmailAddress")).strip("'") or None,
                "phone": to_str(row.get("chrPhoneNumber")) or None,
                "cell": None,
            })
    if profile_type == "customer":
        name2 = f"{to_str(row.get('chrFirstName2'))} {to_str(row.get('chrLastName2'))}".strip()
        if name2:
            contacts.append(_contact(name2, row.get("chrTitle2"), row.get("chrEmailAddress2"),
                                     row.get("chrPhoneNumber2"), row.get("chrCell2")))
    return profile, contacts


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #

def transform_project(row: dict[str, Any]) -> dict[str, Any]:
    """tblProjects -> Project. UCA number is reformatted to A#### (padded) when
    numeric; blnArchive -> status. Duplicate-UCA and client-resolution handling
    is stateful, so it stays in the reconciler/sync, not this per-row function.

    Status reads the real staged BOOLEAN via ``to_bool`` (staging maps Access
    boolean -> Postgres BOOLEAN). The importer compared the CSV string
    ``== "1"``, which is fragile for other boolean encodings; reading the actual
    boolean is the correct source semantics -- a deliberate, documented divergence.
    """
    legacy_id = opt_int(row.get("ProjectID"))
    uca_raw = to_str(row.get("UCAProjectNr")) or (str(legacy_id) if legacy_id else "")
    try:
        uca = f"A{int(uca_raw):04d}"
    except ValueError:
        uca = uca_raw  # already prefixed or non-numeric
    return {
        "project_legacy_id": legacy_id,
        "name": to_str(row.get("ProjectName")) or (f"Project {legacy_id}" if legacy_id else None),
        "client_legacy_id": opt_int(row.get("ClientID")),
        "uca_project_number": uca or None,
        "ucsh_project_number": to_str(row.get("UCSHProjectNr")) or None,
        "created_on": to_datetime(row.get("dtmStartDate")),
        "status": "archived" if to_bool(row.get("blnArchive")) else "active",
        "project_lead": to_str(row.get("EmployeeID")) or None,  # a name string, not an FK
    }


# --------------------------------------------------------------------------- #
# Purchase orders (G2: derive close-state instead of hardcoding closed)
# --------------------------------------------------------------------------- #

def transform_po(row: dict[str, Any]) -> dict[str, Any]:
    """tblPurchaseOrders -> PurchaseOrder header. Status is NOT hardcoded closed
    (G2): the source's ``blnRecievedAll`` (sic -- Vision's misspelling) is carried
    as ``legacy_received_all``, and the true open/closed state is derived from the
    PO lines' received quantities by the reconciler/sync.

    ``vendor_po_number`` is the Vision ``chrPONumber`` text. The free-text
    ``chrDateRequired`` is PRESERVED, not dropped: Vision stores it as text (it is
    literally "ASAP" on every real row), so we FOLD the raw value onto
    ``work_description`` as a "Date required: {value}" line rather than lose it to a
    date parser -- while STILL best-effort-parsing a genuine date string
    ("2019-06-15") into ``expected_delivery_date`` (free text -> None, so no garbage
    date is written). (Vision's ``curDiscount`` is intentionally NOT imported --
    Velocity's PO has no discount field and it is math-adjacent.)
    """
    # Fold the free-text "Date Required" onto the note so the legacy value is kept
    # verbatim (it's a text field, all "ASAP"), instead of dropping it when it fails
    # to parse as a date. The parsed date (when one exists) still lands in the real
    # expected_delivery_date column below.
    note = to_str(row.get("memNote"))                       # the PO's own note (may be blank)
    date_required = to_str(row.get("chrDateRequired"))      # free-text "Date Required" (usually "ASAP")
    if date_required:                                       # preserve it verbatim on the note
        line = f"Date required: {date_required}"
        note = f"{note}\n{line}" if note else line          # append to an existing note, else stand alone
    return {
        "po_legacy_id": opt_int(row.get("PurchaseOrderID")),
        "project_legacy_id": opt_int(row.get("intProjectID")),
        "vendor_legacy_id": opt_int(row.get("intVendorID")),
        "created_at": to_datetime(row.get("dtmOrderDate")),
        "work_description": note or None,                   # memNote + folded-in "Date required: ..."
        "vendor_po_number": to_str(row.get("chrPONumber")) or None,     # Vision "P/O Number" text
        "expected_delivery_date": to_datetime(row.get("chrDateRequired")),  # genuine date -> date, else None
        "legacy_received_all": to_bool(row.get("blnRecievedAll")),
    }


def transform_po_line(row: dict[str, Any]) -> dict[str, Any]:
    """tblPurchaseOrdersMaterial -> POLineItem. qty_received sums the three Vision
    receipt columns; qty_pending is the shortfall (mirrors the importer, which
    already reads these -- unlike the PO status)."""
    quantity = max(1, to_int(row.get("intQtyOrdered"), default=1))
    received = (to_int(row.get("intQtyReceived1"))
                + to_int(row.get("intQtyReceived2"))
                + to_int(row.get("intQtyReceived3")))
    return {
        # The PO line's OWN Vision primary key (PurchaseOrderPartID) -- the stable
        # legacy_id the write path keys on for idempotent re-runs.
        "line_legacy_id": opt_int(row.get("PurchaseOrderPartID")),
        "po_legacy_id": opt_int(row.get("intPurchaseOrderID")),
        "item_type": "part",
        "part_legacy_id": opt_int(row.get("intProductID")),
        "description": to_str(row.get("chrProductDescription")) or None,
        "quantity": quantity,
        "unit_price": to_float(row.get("curUnitPrice")),
        "qty_received": received,
        "qty_pending": max(0, quantity - received),
    }


# --------------------------------------------------------------------------- #
# Staff (from tblEmployees)
# --------------------------------------------------------------------------- #
# Vision's real staff table is tblEmployees (41 rows), keyed by EmployeeID. Each row
# becomes one 'staff' Profile. We key on EmployeeID -- NOT the name -- so two people
# who share a name (the source has two distinct "Cory DaSilva" rows, ids 72 and 73)
# stay separate, and a re-run UPDATEs by key rather than duplicating.
#
# Sparse-source reality (from the export): First/Last name are always present;
# Address (3/41), Postal Code (2/41), Home Phone (2/41), Mobile (2/41) and Email
# (8/41) are mostly blank. Blank -> None (address/postal are nullable as of migration
# 025; empty contact fields are omitted). Role flags (Lead/Installer/Manager/...) and
# City ('Toronto' for all) are dropped -- Velocity's Profile has no field for them.

STAFF_LEGACY_SOURCE = "tblEmployees"   # real Vision staff table; EmployeeID is its primary key


def transform_staff(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Map one staged ``tblEmployees`` row to a 'staff' Profile dict (+ one contact).

    Keyed on the real Vision primary key ``EmployeeID`` (same-name people stay
    distinct; re-runs UPDATE by key). Address/postal are imported when present, else
    ``None`` (Vision stores ``''`` for most staff, and ``Profile.address``/
    ``postal_code`` are nullable as of migration 025). The person's own email, home
    phone and mobile become a single Contact; empty values are omitted rather than
    stored blank. Job roles (``blnLead`` / ``blnInstaller`` / ``blnManager`` ->
    "Manager") are joined into ``staff_roles``; the other booleans
    (``blnListedinToDoForm`` / ``blnSeeYtoDateTotals`` / ``blnRemedial``) are app
    permissions, not roles, and city is dropped -- Velocity's Profile has no field
    for those.

    Args:
        row: One staged ``tblEmployees`` row (``column -> value``; keys are the real
            Access FIELD names, e.g. ``chrFirstName`` / ``blnLead`` -- NOT the
            datasheet captions ("First Name", "Lead") the xlsx export displayed.

    Returns:
        A staff-Profile dict shaped for the import engine and a future write path::

            {staff_legacy_id, type, name, pst, address, postal_code, staff_roles, contacts}

        or ``None`` when the row has no usable ``EmployeeID`` (can't be keyed).
    """
    legacy_id = opt_int(row.get("EmployeeID"))            # real Vision PK -> our legacy_id
    if legacy_id is None:                                 # no key -> can't import safely -> drop
        return None
    first = to_str(row.get("chrFirstName"))               # given name (always present in source)
    last = to_str(row.get("chrLastName"))                 # family name (always present in source)
    # Full name from first+last; fall back to account name, then a stable stub.
    name = " ".join(f"{first} {last}".split()) or to_str(row.get("chrAccountName")) or f"Staff {legacy_id}"
    # The staff member IS their own contact: carry email + both phones; the helper
    # turns empties into None (phone -> ContactPhone(work), cell -> ContactPhone(mobile)).
    contact = _contact(
        name,
        None,                                             # job_title dropped: roles live in staff_roles below
        row.get("chrEmailAddress"),                       # -> Contact.email
        row.get("chrHomePhone"),                          # -> ContactPhone(work)
        row.get("chrMobile"),                             # -> ContactPhone(mobile)
    )
    # Job ROLES from the three role booleans, in a stable order. Only these three are
    # roles; the other flags (blnListedinToDoForm / blnSeeYtoDateTotals / blnRemedial)
    # are app permissions, not roles, so they are ignored. A person may hold several.
    # to_bool copes with the Access yes/no encodings (True/False, 1/0, "1"/"0", -1).
    roles = []                                            # collected in fixed order for a stable string
    if to_bool(row.get("blnLead")):                       # Vision blnLead flag
        roles.append("Lead")
    if to_bool(row.get("blnInstaller")):                  # Vision blnInstaller flag
        roles.append("Installer")
    if to_bool(row.get("blnManager")):                    # Vision blnManager -> "Manager"
        roles.append("Manager")
    staff_roles = ", ".join(roles) or None                # "Lead, Manager" etc.; no roles -> None
    return {
        "staff_legacy_id": legacy_id,                     # tblEmployees.EmployeeID (update/adopt key)
        "type": "staff",                                  # ProfileType.staff (added by #172)
        "name": name,                                     # display name "First Last"
        "pst": None,                                      # staff have no PST
        "address": to_str(row.get("chrAddress")) or None, # nullable; Vision '' -> None
        "postal_code": to_str(row.get("chrPostalCode")) or None,
        "staff_roles": staff_roles,                       # comma-joined job roles, or None
        "contacts": [contact],                            # exactly one contact: the staff member
    }


def extract_staff(employee_rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transform staged ``tblEmployees`` rows into staff-Profile dicts.

    One Profile per employee, keyed on the real ``EmployeeID`` (no name
    de-duplication -- same-name employees are distinct people). Rows with no
    ``EmployeeID`` are dropped.

    Args:
        employee_rows: Staged ``tblEmployees`` rows (``column -> value`` dicts).

    Returns:
        One staff-Profile dict per keyable row, in input order (shape per
        :func:`transform_staff`).
    """
    staff: list[dict[str, Any]] = []
    for row in employee_rows:                             # single pass (may be a generator)
        rec = transform_staff(row)                        # None when the row has no EmployeeID
        if rec is not None:
            staff.append(rec)
    return staff
