"""Unit tests for legacy-import helpers.

Issue #54: the legacy UC Vision WorkorderID (its quote-number) must be
prepended as "[WO {id}]" to the very start of a migrated quote's work
description. These tests exercise the pure prefixing helper directly,
without standing up the full /import integration path.

Issue #164: the close-state helpers must read Vision's quantities as stored
(fractions, zero and negative kept), derive a line's fulfilled / pending split
from Vision's real ship fields, honour a work order Vision finished (all shipped
or force-closed), and feed the app's own status rules. The full endpoint path is
covered in ``test_migration_import_endpoint.py``.
"""
from datetime import datetime
from types import SimpleNamespace

from routes.migration import (
    wo_prefixed_description,
    parse_bool, opt_int, opt_qty, line_quantity, derive_line_fulfillment,
    workorder_closed, workorder_force_closed, line_close_state,
    whole_po_quantity, workorder_created,
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


def test_opt_qty_keeps_fraction_sign_and_zero():
    # Vision quantities are Access Single: read as floats, rounded to 2 decimals.
    assert opt_qty("1.5") == 1.5           # fraction kept (was floored to an int)
    assert opt_qty("2.333") == 2.33        # rounded to the 2 decimals the app stores
    assert opt_qty("3.0") == 3.0
    assert opt_qty("0") == 0.0             # quoted, not ordered: a real zero
    assert opt_qty("-2") == -2.0           # credit line keeps its sign
    assert opt_qty(" 1,200 ") == 1200.0    # thousands separator and padding tolerated
    assert str(opt_qty("-0.001")) == "0.0"  # no negative zero leaks through rounding
    assert opt_qty("") is None             # blank -> not recorded
    assert opt_qty(None) is None
    assert opt_qty("nope") is None         # garbage -> not recorded


def test_line_quantity_reads_vision_quantity_as_stored():
    # Fraction, zero and negative arrive as-is (no more floor at 1).
    assert line_quantity({"intQuantity": "1.5"}, "intQuantity") == 1.5
    assert line_quantity({"intQuantity": "0"}, "intQuantity") == 0.0
    assert line_quantity({"intQuantity": "-2"}, "intQuantity") == -2.0
    assert line_quantity({"intQtyOrdered": "7"}, "intQtyOrdered") == 7.0   # PO field name too
    # Blank / missing / unparseable keep the import's old default of 1.
    assert line_quantity({"intQuantity": ""}, "intQuantity") == 1.0
    assert line_quantity({}, "intQuantity") == 1.0
    assert line_quantity({"intQuantity": "n/a"}, "intQuantity") == 1.0


def test_whole_po_quantity_rounds_halves_away_from_zero():
    # PO lines hold whole numbers; halves go away from zero (Python's round() would give 2 for 2.5).
    assert whole_po_quantity(2.5) == 3
    assert whole_po_quantity(46.5) == 47
    assert whole_po_quantity(17.5) == 18
    assert whole_po_quantity(2.4) == 2                       # below a half rounds down as usual
    assert whole_po_quantity(-2.5) == -3                     # credit keeps its sign, half away from zero
    assert whole_po_quantity(0) == 0                         # a real zero stays zero
    # A nonzero quantity never collapses to 0, so a 0.4 line still has something to receive.
    assert whole_po_quantity(0.4) == 1
    assert whole_po_quantity(-0.4) == -1
    assert isinstance(whole_po_quantity(2.5), int)           # the PO columns are Integer


def test_workorder_created_prefers_start_then_entered():
    # dtmDateStarted wins when present.
    assert workorder_created(
        {"dtmDateStarted": "1/3/2020 0:00:00", "dtmDateEntered": "1/1/2020 0:00:00"}
    ) == datetime(2020, 1, 3)
    # Blank (or unparseable) start falls back to the entry date.
    assert workorder_created({"dtmDateStarted": "", "dtmDateEntered": "12/30/2019 0:00:00"}) == datetime(2019, 12, 30)
    assert workorder_created({"dtmDateStarted": "n/a", "dtmDateEntered": "12/30/2019"}) == datetime(2019, 12, 30)
    assert workorder_created({"dtmDateEntered": "12/30/2019"}) == datetime(2019, 12, 30)   # start column absent
    # Both blank -> None (the import then falls back to "now").
    assert workorder_created({"dtmDateStarted": "", "dtmDateEntered": ""}) is None
    assert workorder_created({}) is None


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


def test_fulfillment_keeps_fractions():
    # 0.5 of 1.5 shipped -> 0.5 fulfilled, 1.0 pending (was truncated to ints).
    assert derive_line_fulfillment({"intTotalShippedQuantity": "0.5"}, 1.5) == (0.5, 1.0)
    # Pending is rounded, so float noise never persists (1.5 - 0.7 is not exactly 0.8).
    assert derive_line_fulfillment({"intShipQuantity": "0.7"}, 1.5) == (0.7, 0.8)
    # Fractional back-order: 1.5 ordered, 0.25 back-ordered -> 1.25 shipped.
    assert derive_line_fulfillment({"intQuantityBO": "0.25"}, 1.5) == (1.25, 0.25)


def test_fulfillment_clamps_over_shipped_and_negative_shipped():
    # Over-shipped fractional line clamps to the ordered quantity.
    assert derive_line_fulfillment({"intTotalShippedQuantity": "2.5"}, 1.5) == (1.5, 0.0)
    # A negative shipped total on a positive line is bad data -> clamps to nothing shipped.
    assert derive_line_fulfillment({"intTotalShippedQuantity": "-3"}, 10) == (0.0, 10.0)


def test_fulfillment_only_positive_backorder_is_a_signal():
    # Negative or zero back-order with no shipped field -> no signal at all.
    assert derive_line_fulfillment({"intQuantityBO": "-2"}, 10) is None
    assert derive_line_fulfillment({"intQuantityBO": "0"}, 10) is None
    assert derive_line_fulfillment({"intQuantityBO": ""}, 10) is None


def test_workorder_closed_predicate():
    # blnAllShipped is Vision's "finished" flag; Access encodes TRUE as -1, 1 or True.
    assert workorder_closed({"blnAllShipped": "-1"}) is True
    assert workorder_closed({"blnAllShipped": "1"}) is True
    assert workorder_closed({"blnAllShipped": "True"}) is True
    # A force-close also finishes the job, by flag or by status text.
    assert workorder_closed({"blnAllShipped": "0", "blnForceClosed": "-1"}) is True
    assert workorder_closed({"chrStatus": "Force Closed by DPowell."}) is True
    # Flag false or blank, no force-close -> not finished (the lines decide).
    assert workorder_closed({"blnAllShipped": "0", "blnForceClosed": "0", "chrStatus": "Open"}) is False
    assert workorder_closed({"blnAllShipped": "", "chrStatus": "Closed"}) is False   # text "Closed" alone is not a signal
    assert workorder_closed({}) is False


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
    assert line_close_state(partial, 10, closed=False) == (4, 6, True)
    # Finished header (all shipped or force-closed) overrides the partial shipment: whole line done.
    assert line_close_state(partial, 10, closed=True) == (10, 0, True)
    # No ship data at all -> nothing claimed done, flagged as no data.
    assert line_close_state({}, 5, closed=False) == (0, 5, False)


def test_line_close_state_zero_line_has_nothing_to_do():
    # Quoted, not ordered: nothing fulfilled, nothing pending, whatever the ship fields or header say.
    assert line_close_state({}, 0.0, closed=False) == (0.0, 0.0, True)
    assert line_close_state({"intTotalShippedQuantity": "5"}, 0.0, closed=False) == (0.0, 0.0, True)
    assert line_close_state({}, 0.0, closed=True) == (0.0, 0.0, True)


def test_line_close_state_negative_line_is_never_pending():
    # Credit line, open job, never shipped -> nothing fulfilled, nothing pending.
    assert line_close_state({}, -2.0, closed=False) == (0.0, 0.0, True)
    assert line_close_state({"intTotalShippedQuantity": "0"}, -2.0, closed=False) == (0.0, 0.0, True)  # zero is not "shipped"
    # Open credit as Vision stores it: single shipment = quantity, running total 0.
    # The recorded total wins, so it is NOT processed (would wrongly settle open jobs).
    assert line_close_state(
        {"intTotalShippedQuantity": "0", "intShipQuantity": "-1"}, -1.0, closed=False
    ) == (0.0, 0.0, True)
    # Credit line Vision processed (nonzero total or single shipment) -> fulfilled at its own quantity.
    assert line_close_state({"intTotalShippedQuantity": "-2"}, -2.0, closed=False) == (-2.0, 0.0, True)
    assert line_close_state({"intShipQuantity": "1"}, -2.0, closed=False) == (-2.0, 0.0, True)
    # Credit line on a finished job -> fulfilled even with no ship data.
    assert line_close_state({}, -2.0, closed=True) == (-2.0, 0.0, True)


def test_line_close_state_closed_header_fulfils_whole_positive_line():
    # Fractional line, partly shipped, on a finished job -> the whole 1.5 is done.
    assert line_close_state({"intTotalShippedQuantity": "0.5"}, 1.5, closed=True) == (1.5, 0.0, True)
    # No ship data on a finished job -> still done, and NOT counted as missing ship data.
    assert line_close_state({}, 3.0, closed=True) == (3.0, 0.0, True)


def test_line_close_state_open_fractional_line():
    # Open job: the ship fields decide, fractions kept.
    assert line_close_state({"intTotalShippedQuantity": "0.5"}, 1.5, closed=False) == (0.5, 1.0, True)
    # Open job, no ship data -> fully pending, flagged.
    assert line_close_state({"intQuantityBO": "0"}, 1.5, closed=False) == (0, 1.5, False)


def _line(fulfilled: int, pending: int) -> SimpleNamespace:
    return SimpleNamespace(qty_fulfilled=fulfilled, qty_pending=pending, quantity=fulfilled + pending)


def _imported_line(row: dict, quantity: float, closed: bool) -> SimpleNamespace:
    """Build an in-memory line the way the import does, via ``line_close_state``.

    Args:
        row: The line's CSV row (ship fields).
        quantity: The ordered quantity, as Vision has it.
        closed: True when the owning work order is finished in Vision.

    Returns:
        An object with ``quantity`` / ``qty_fulfilled`` / ``qty_pending``, enough
        for ``compute_status_from_lines``.
    """
    fulfilled, pending, _ = line_close_state(row, quantity, closed)   # the import's own split
    return SimpleNamespace(quantity=quantity, qty_fulfilled=fulfilled, qty_pending=pending)


def test_zero_and_credit_lines_do_not_hold_an_open_quote_open():
    # Open job: positive line fully shipped, plus a zero line and an unshipped credit line.
    lines = [
        _imported_line({"intTotalShippedQuantity": "4"}, 4.0, closed=False),   # shipped in full
        _imported_line({}, 0.0, closed=False),                                 # quoted, not ordered
        _imported_line({}, -1.0, closed=False),                                # credit, not processed
    ]
    assert compute_status_from_lines(lines, None) == "Closed"
    # Same quote but the positive line is only half shipped -> still open (Invoiced).
    lines[0] = _imported_line({"intTotalShippedQuantity": "2"}, 4.0, closed=False)
    assert compute_status_from_lines(lines, None) == "Invoiced"


def test_fractional_partial_shipment_lands_invoiced():
    # 0.5 of 1.5 shipped on an open job -> something fulfilled, something pending.
    line = _imported_line({"intTotalShippedQuantity": "0.5"}, 1.5, closed=False)
    assert (line.qty_fulfilled, line.qty_pending) == (0.5, 1.0)
    assert compute_status_from_lines([line], None) == "Invoiced"


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
