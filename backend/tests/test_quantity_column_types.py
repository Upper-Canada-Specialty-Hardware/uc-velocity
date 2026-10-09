"""Guard tests for fractional quote/invoice quantities that need no live database.

Covers:
- every live quantity column and its history (snapshot) twin share one type,
  so a snapshot can never silently round a value the live line holds
- purchase order, PO history and PO receiving quantities are still whole numbers
- the shared schema validator (positive, at most 2 decimals, float noise tolerated)
- read-only response schemas stay lenient and pass stored values through
- the audit-text formatter ("2" not "2.0", "1.5" as-is)

Importing the models only needs ``DATABASE_URL`` set (the engine is created
lazily and never connects here), so the module skips when it is absent.
"""
import os

import pytest

if not os.getenv("DATABASE_URL"):
    # models -> database raises at import without DATABASE_URL; CI always sets it
    pytest.skip("DATABASE_URL not set", allow_module_level=True)

from sqlalchemy import Float, Integer

from models import (
    QuoteLineItem, QuoteLineItemSnapshot,
    InvoiceLineItem, InvoiceLineItemSnapshot,
    POLineItem, POLineItemSnapshot, POReceivingLineItem,
)
from schemas import (
    validate_quantity, coerce_quantity_for_read,
    InvoiceLineItem as InvoiceLineItemSchema,
    QuoteLineItemSnapshot as QuoteLineItemSnapshotSchema,
)
from routes.quotes import format_quantity, round_quantity

QUOTE_QTY_COLUMNS = ["quantity", "qty_pending", "qty_fulfilled"]
INVOICE_QTY_COLUMNS = [
    "qty_ordered", "qty_fulfilled_this_invoice", "qty_fulfilled_total", "qty_pending_after",
]


def _col_type(model, name):
    """Return the SQLAlchemy type class of ``model.name``.

    Args:
        model: A declarative model class.
        name: Column name on its table.

    Returns:
        The column's type class (e.g. ``Float``).
    """
    return type(model.__table__.c[name].type)


@pytest.mark.parametrize("name", QUOTE_QTY_COLUMNS)
def test_quote_line_and_snapshot_share_float_type(name):
    """Quote line quantity columns and their snapshot twins are both Float."""
    assert _col_type(QuoteLineItem, name) is Float
    assert _col_type(QuoteLineItemSnapshot, name) is _col_type(QuoteLineItem, name)


@pytest.mark.parametrize("name", INVOICE_QTY_COLUMNS)
def test_invoice_line_and_snapshot_share_float_type(name):
    """Invoice line quantity columns and their snapshot twins are both Float."""
    assert _col_type(InvoiceLineItem, name) is Float
    assert _col_type(InvoiceLineItemSnapshot, name) is _col_type(InvoiceLineItem, name)


@pytest.mark.parametrize("model,name", [
    (POLineItem, "quantity"), (POLineItem, "qty_pending"), (POLineItem, "qty_received"),
    (POLineItemSnapshot, "quantity"), (POLineItemSnapshot, "qty_pending"),
    (POLineItemSnapshot, "qty_received"),
    (POReceivingLineItem, "qty_ordered"), (POReceivingLineItem, "qty_received_this_receiving"),
    (POReceivingLineItem, "qty_received_total"), (POReceivingLineItem, "qty_pending_after"),
])
def test_purchase_order_quantities_stay_integer(model, name):
    """PO quantities are out of scope for fractions and must remain Integer."""
    assert _col_type(model, name) is Integer


@pytest.mark.parametrize("raw,expected", [
    (1, 1.0), (1.5, 1.5), (0.25, 0.25), ("2.75", 2.75),
    (0.1 + 0.2, 0.3),  # binary float noise is tolerated and normalised
])
def test_validate_quantity_accepts_up_to_two_decimals(raw, expected):
    """Positive values with at most 2 decimals pass and come back rounded."""
    assert validate_quantity(raw) == expected


@pytest.mark.parametrize("raw", [1.234, 0, -1, 0.001, "abc", True, float("nan"), float("inf")])
def test_validate_quantity_rejects_bad_values(raw):
    """Three decimals, zero/negative, non-numbers and NaN/inf are all refused."""
    with pytest.raises(ValueError):
        validate_quantity(raw)


def test_validate_quantity_allow_zero_for_running_totals():
    """Pending/fulfilled totals may be 0 but never negative; None passes through."""
    assert validate_quantity(0, allow_zero=True) == 0.0
    assert validate_quantity(None) is None
    with pytest.raises(ValueError):
        validate_quantity(-0.5, allow_zero=True)


@pytest.mark.parametrize("raw,expected", [(None, None), (2, 2.0), (-1, -1.0), (1.333, 1.333)])
def test_coerce_quantity_for_read_never_rejects(raw, expected):
    """Read-side coercion passes stored values through unchanged as floats."""
    assert coerce_quantity_for_read(raw) == expected


def test_response_schemas_accept_stored_values_input_rules_refuse():
    """Invoice line and history responses accept -1 and 1.333 (no 500 on odd rows)."""
    inv = InvoiceLineItemSchema(id=1, invoice_id=1, item_type="misc", qty_ordered=1.333,
                                qty_fulfilled_this_invoice=-1, qty_fulfilled_total=1.333,
                                qty_pending_after=-1)
    assert inv.qty_ordered == 1.333 and inv.qty_pending_after == -1.0
    snap = QuoteLineItemSnapshotSchema(id=1, snapshot_id=1, item_type="misc", quantity=1.333,
                                       qty_pending=-1, qty_fulfilled=0)
    assert snap.quantity == 1.333 and snap.qty_pending == -1.0


@pytest.mark.parametrize("qty,text", [
    (2, "2"), (2.0, "2"), (1.5, "1.5"), (0.25, "0.25"), (1.1 + 2.2, "3.3"), (None, "0"),
    (12345.25, "12345.25"), (99999.99, "99999.99"), (123456.5, "123456.5"),  # no 6-digit cut-off
])
def test_format_quantity(qty, text):
    """Whole quantities read as integers; fractions keep only their real digits."""
    assert format_quantity(qty) == text


def test_round_quantity_lands_exactly_on_zero():
    """Repeated fractional invoicing must leave pending at exactly 0.0."""
    assert round_quantity(round_quantity(1.5 - 0.7) - 0.8) == 0.0
    assert round_quantity(0.3 - 0.1 - 0.2) == 0.0  # raw result is -2.7e-17
