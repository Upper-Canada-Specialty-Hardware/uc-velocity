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
from typing import Any, Optional


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


def transform_workorder_line(row: dict[str, Any], item_type: str) -> dict[str, Any]:
    """Map one staged workorder-line row to a Velocity quote line-item dict.

    ``item_type`` is one of ``"labor"`` / ``"part"`` / ``"misc"``. The returned
    dict carries the *legacy* reference id (resolved to a Velocity id only at
    write time) and the real fulfillment split. When the row has no ship data at
    all, we default to fully PENDING and flag ``ship_data_present=False`` -- the
    conservative choice (don't claim work was done when Vision didn't record it),
    surfaced so the reconciler can report how much of the catalogue is uncertain.
    """
    if item_type not in _LINE_SPECS:
        raise ValueError(f"unknown item_type {item_type!r}")
    spec = _LINE_SPECS[item_type]

    quantity = max(1, to_int(row.get("intQuantity"), default=1))
    fulfillment = derive_line_fulfillment(row, quantity)
    if fulfillment is None:
        qty_fulfilled, qty_pending, ship_present = 0, quantity, False
    else:
        qty_fulfilled, qty_pending = fulfillment
        ship_present = True

    return {
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
