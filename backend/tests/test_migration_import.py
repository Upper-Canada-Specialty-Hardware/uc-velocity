"""Unit tests for legacy-import helpers.

Issue #54: the legacy UC Vision WorkorderID (its quote-number) must be
prepended as "[WO {id}]" to the very start of a migrated quote's work
description. These tests exercise the pure prefixing helper directly,
without standing up the full /import integration path.

Issue #164: the close-state helpers must derive a line's fulfilled / pending
split from Vision's real ship fields, honour a force-closed work order, and
feed the app's own status rules. The full endpoint path is covered in
``test_migration_import_endpoint.py``.
"""
from types import SimpleNamespace

from routes.migration import (
    wo_prefixed_description,
    parse_bool, opt_int, derive_line_fulfillment, workorder_force_closed, line_close_state,
)
from routes.quotes import compute_status_from_lines
from routes.purchase_orders import compute_po_status
from models import POStatus


def test_wo_prefix_with_description():
    assert (
        wo_prefixed_description(29, "Install 2 hidden switches")
        == "[WO 29] Install 2 hidden switches"
    )


def test_wo_prefix_strips_surrounding_whitespace():
    # safe_str strips, so leading/trailing whitespace and newlines are normalized
    assert wo_prefixed_description(29, "  \n Arrive on site \n ") == "[WO 29] Arrive on site"


def test_wo_prefix_preserves_internal_newlines():
    raw = "Line one.\nLine two."
    assert wo_prefixed_description(100, raw) == "[WO 100] Line one.\nLine two."


def test_wo_prefix_empty_description_collapses_to_tag():
    assert wo_prefixed_description(29, "") == "[WO 29]"
    assert wo_prefixed_description(29, "   ") == "[WO 29]"


def test_wo_prefix_none_description_collapses_to_tag():
    assert wo_prefixed_description(7, None) == "[WO 7]"


# ---- Close-state helpers (issue #164) -------------------------------------

def test_parse_bool_access_encodings():
    # Access exports TRUE as -1 (or 1 / True / Yes) and FALSE as 0 (or False / No).
    assert parse_bool("-1") is True
    assert parse_bool("1") is True
    assert parse_bool("True") is True
    assert parse_bool("0") is False
    assert parse_bool("False") is False
    assert parse_bool("") is None          # blank -> not recorded
    assert parse_bool(None) is None
    assert parse_bool("maybe") is None


def test_opt_int_distinguishes_zero_from_missing():
    assert opt_int("0") == 0               # a real zero stays zero
    assert opt_int("3.0") == 3
    assert opt_int("") is None             # blank -> not recorded
    assert opt_int(None) is None
    assert opt_int("nope") is None


def test_fulfillment_from_total_shipped_partial():
    assert derive_line_fulfillment({"intTotalShippedQuantity": "3"}, 10) == (3, 7)


def test_fulfillment_falls_back_to_ship_quantity_then_backorder():
    assert derive_line_fulfillment({"intShipQuantity": "4"}, 10) == (4, 6)
    # No shipped fields, but back-order says 2 of 10 remain -> 8 shipped.
    assert derive_line_fulfillment({"intQuantityBO": "2"}, 10) == (8, 2)


def test_fulfillment_backorder_zero_is_not_a_shipped_signal():
    # A bare back-order of 0 (Vision's default/empty) must NOT be read as fully
    # shipped when no shipped field is present -- that would close never-shipped lines.
    assert derive_line_fulfillment({"intQuantityBO": "0"}, 10) is None
    # But an explicit shipped total of 0 IS a real signal -> nothing shipped.
    assert derive_line_fulfillment({"intTotalShippedQuantity": "0"}, 10) == (0, 10)


def test_fulfillment_prefers_total_over_other_signals():
    row = {"intTotalShippedQuantity": "5", "intShipQuantity": "1", "intQuantityBO": "9"}
    assert derive_line_fulfillment(row, 10) == (5, 5)


def test_fulfillment_none_when_no_ship_data():
    assert derive_line_fulfillment({}, 10) is None
    assert derive_line_fulfillment({"intTotalShippedQuantity": ""}, 10) is None


def test_fulfillment_clamps_out_of_range():
    # Over-shipped legacy data clamps to quantity; garbage negatives clamp to 0.
    assert derive_line_fulfillment({"intTotalShippedQuantity": "99"}, 10) == (10, 0)
    assert derive_line_fulfillment({"intQuantityBO": "99"}, 10) == (0, 10)


def test_workorder_force_closed_predicate():
    # Either signal counts, any casing; free-text "Force Closed by X." variants too.
    assert workorder_force_closed({"blnForceClosed": "-1"}) is True
    assert workorder_force_closed({"chrStatus": "Force Closed by DPowell."}) is True
    assert workorder_force_closed({"chrStatus": "FORCE CLOSED"}) is True
    # A normally-closed or open work order is NOT force-closed.
    assert workorder_force_closed({"chrStatus": "Closed", "blnForceClosed": "0"}) is False
    assert workorder_force_closed({"chrStatus": "Open"}) is False
    assert workorder_force_closed({}) is False


def test_line_close_state_paths():
    partial = {"intTotalShippedQuantity": "4"}
    assert line_close_state(partial, 10, force_closed=False) == (4, 6, True)
    # Force-closed header overrides the partial shipment: whole line done.
    assert line_close_state(partial, 10, force_closed=True) == (10, 0, True)
    # No ship data at all -> nothing claimed done, flagged as no data.
    assert line_close_state({}, 5, force_closed=False) == (0, 5, False)


def _line(fulfilled: int, pending: int) -> SimpleNamespace:
    return SimpleNamespace(qty_fulfilled=fulfilled, qty_pending=pending, quantity=fulfilled + pending)


def test_quote_status_rule_from_lines():
    # The rule the import feeds: Closed / Invoiced / Work Order / Draft.
    assert compute_status_from_lines([_line(4, 0)], None) == "Closed"
    assert compute_status_from_lines([_line(3, 7)], "PO-1") == "Invoiced"
    assert compute_status_from_lines([_line(0, 5)], "PO-1") == "Work Order"
    assert compute_status_from_lines([_line(0, 5)], None) == "Draft"
    assert compute_status_from_lines([], "PO-1") == "Work Order"   # no lines -> never Closed


def test_po_status_rule_from_lines():
    assert compute_po_status([_line(4, 0)]) is POStatus.received
    assert compute_po_status([_line(3, 7)]) is POStatus.sent
    assert compute_po_status([_line(0, 2)]) is POStatus.draft
    assert compute_po_status([]) is POStatus.draft                 # no lines -> nothing received
