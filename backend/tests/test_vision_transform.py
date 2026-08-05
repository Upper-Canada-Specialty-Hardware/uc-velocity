"""Unit tests for the Vision -> Velocity transform (compute side).

These exercise the pure mapping functions in
``scripts.vision_migration.transform`` with plain dict fixtures -- no database
and no ``pywin32`` -- so they run locally and on Linux CI. Fixtures use the
Python types the staged ``vision_legacy`` schema actually returns (``Decimal``
for currency, ``datetime`` for dates, ``bool`` for Access booleans) as well as
raw strings, to prove the coercion helpers tolerate both.

The headline behaviour under test is the G1 correction: a partially-shipped
workorder line must derive ``qty_pending > 0`` from the real ship fields, rather
than being forced fully-fulfilled the way the original CSV importer did.
"""
from datetime import datetime
from decimal import Decimal

from scripts.vision_migration import transform as t


# --------------------------------------------------------------------------- #
# Coercion helpers
# --------------------------------------------------------------------------- #

def test_to_int_rounds_and_defaults():
    assert t.to_int("5") == 5
    assert t.to_int(Decimal("4.6")) == 5
    assert t.to_int(3.2) == 3
    assert t.to_int(None) == 0
    assert t.to_int("", default=7) == 7
    assert t.to_int("garbage", default=-1) == -1
    assert t.to_int(True) == 1  # bool handled explicitly


def test_opt_int_distinguishes_zero_from_missing():
    # The key property: a real 0 stays 0, but absent/blank -> None.
    assert t.opt_int(0) == 0
    assert t.opt_int("0") == 0
    assert t.opt_int(None) is None
    assert t.opt_int("") is None
    assert t.opt_int("   ") is None
    assert t.opt_int("nope") is None


def test_to_float_handles_decimal_currency_and_accounting_negatives():
    assert t.to_float(Decimal("12.50")) == 12.5
    assert t.to_float("$1,234.56") == 1234.56
    assert t.to_float("(722.68)") == -722.68  # accounting-style negative
    assert t.to_float(None) == 0.0
    assert t.to_float("") == 0.0


def test_to_bool_access_style():
    assert t.to_bool(True) is True
    assert t.to_bool(-1) is True   # Access TRUE is -1
    assert t.to_bool("-1") is True
    assert t.to_bool(0) is False
    assert t.to_bool("false") is False
    assert t.to_bool(None) is None
    assert t.to_bool("maybe") is None


def test_to_datetime_passthrough_and_parse():
    dt = datetime(2004, 11, 12)
    assert t.to_datetime(dt) is dt
    assert t.to_datetime("11/12/2004 0:00:00") == datetime(2004, 11, 12)
    assert t.to_datetime("2004-11-12") == datetime(2004, 11, 12)
    assert t.to_datetime(None) is None
    assert t.to_datetime("not a date") is None


def test_wo_prefixed_description_matches_importer_behaviour():
    # Mirrors backend/tests/test_migration_import.py to keep parity auditable.
    assert t.wo_prefixed_description(29, "Install switches") == "[WO 29] Install switches"
    assert t.wo_prefixed_description(29, "  \n Arrive \n ") == "[WO 29] Arrive"
    assert t.wo_prefixed_description(29, "") == "[WO 29]"
    assert t.wo_prefixed_description(7, None) == "[WO 7]"


# --------------------------------------------------------------------------- #
# derive_line_fulfillment (the G1 core)
# --------------------------------------------------------------------------- #

def test_fulfillment_from_total_shipped_partial():
    assert t.derive_line_fulfillment({"intTotalShippedQuantity": 3}, 10) == (3, 7)


def test_fulfillment_falls_back_to_ship_quantity():
    assert t.derive_line_fulfillment({"intShipQuantity": 4}, 10) == (4, 6)


def test_fulfillment_infers_from_backorder():
    # No shipped fields, but back-order says 2 of 10 remain -> 8 shipped.
    assert t.derive_line_fulfillment({"intQuantityBO": 2}, 10) == (8, 2)


def test_fulfillment_none_when_no_ship_data():
    assert t.derive_line_fulfillment({}, 10) is None


def test_fulfillment_clamps_out_of_range():
    # Over-shipped legacy data clamps to quantity; garbage negatives clamp to 0.
    assert t.derive_line_fulfillment({"intTotalShippedQuantity": 99}, 10) == (10, 0)
    assert t.derive_line_fulfillment({"intQuantityBO": 99}, 10) == (0, 10)


def test_fulfillment_prefers_total_over_ship_and_bo():
    row = {"intTotalShippedQuantity": 5, "intShipQuantity": 1, "intQuantityBO": 9}
    assert t.derive_line_fulfillment(row, 10) == (5, 5)


# --------------------------------------------------------------------------- #
# transform_workorder_line
# --------------------------------------------------------------------------- #

def test_labor_line_partial_ship_is_the_g1_fix():
    """A partially-shipped labour line yields pending qty -- the whole point.

    The original importer would have written qty_fulfilled=10, qty_pending=0.
    """
    row = {
        "intWorkorderID": 42,
        "intProductName": 7,
        "chrProductDescription": "Install hinges",
        "intQuantity": 10,
        "curUnitPrice": Decimal("25.00"),
        "curNetPrice": Decimal("18.00"),
        "intTotalShippedQuantity": 3,
    }
    line = t.transform_workorder_line(row, "labor")
    assert line["item_type"] == "labor"
    assert line["workorder_legacy_id"] == 42
    assert line["labor_legacy_id"] == 7
    assert line["description"] == "Install hinges"
    assert line["quantity"] == 10
    assert line["unit_price"] == 25.0
    assert line["base_cost"] == 18.0
    assert line["qty_fulfilled"] == 3
    assert line["qty_pending"] == 7
    assert line["ship_data_present"] is True


def test_line_no_ship_data_defaults_fully_pending():
    row = {"intWorkorderID": 1, "intProductName": 2, "intQuantity": 4, "curUnitPrice": "10", "curNetPrice": "8"}
    line = t.transform_workorder_line(row, "part")
    assert line["item_type"] == "part"
    assert line["part_legacy_id"] == 2
    assert line["qty_fulfilled"] == 0
    assert line["qty_pending"] == 4
    assert line["ship_data_present"] is False


def test_line_fully_shipped_is_fully_fulfilled():
    row = {"intWorkorderID": 1, "intQuantity": 4, "intTotalShippedQuantity": 4}
    line = t.transform_workorder_line(row, "labor")
    assert line["qty_fulfilled"] == 4
    assert line["qty_pending"] == 0


def test_misc_line_uses_zone_and_distance_and_price_columns():
    row = {
        "intWorkorderID": 5,
        "chrZones": 12,          # misc reference key is chrZones
        "chrDistance": "0-50km", # misc description column
        "intQuantity": 1,
        "curPrice": Decimal("40.00"),  # misc price column is curPrice, not curUnitPrice
        "curNetPrice": Decimal("30.00"),
        "intTotalShippedQuantity": 1,
    }
    line = t.transform_workorder_line(row, "misc")
    assert line["misc_legacy_id"] == 12
    assert line["description"] == "0-50km"
    assert line["unit_price"] == 40.0
    assert line["base_cost"] == 30.0


def test_quantity_floors_at_one():
    row = {"intWorkorderID": 1, "intQuantity": 0, "intTotalShippedQuantity": 0}
    line = t.transform_workorder_line(row, "labor")
    assert line["quantity"] == 1


def test_unknown_item_type_raises():
    try:
        t.transform_workorder_line({}, "widget")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown item_type")


# --------------------------------------------------------------------------- #
# transform_workorder (quote header)
# --------------------------------------------------------------------------- #

def test_workorder_header_does_not_hardcode_status():
    row = {
        "WorkorderID": 29,
        "PojectID": 100,  # sic: Vision's misspelling, preserved
        "dtmDateStarted": datetime(2004, 11, 12),
        "memWorkDescription": "Install 2 hidden switches",
        "chrStatus": "Open",
    }
    quote = t.transform_workorder(row)
    assert "status" not in quote  # G1: status is NEVER hardcoded here
    assert quote["workorder_legacy_id"] == 29
    assert quote["project_legacy_id"] == 100
    assert quote["created_at"] == datetime(2004, 11, 12)
    assert quote["work_description"] == "[WO 29] Install 2 hidden switches"
    assert quote["legacy_status"] == "Open"


def test_workorder_source_closed_reads_flags_and_status():
    assert t.workorder_source_closed({"blnForceClosed": True}) is True
    assert t.workorder_source_closed({"blnAllShipped": -1}) is True   # Access TRUE
    assert t.workorder_source_closed({"chrStatus": "Closed"}) is True
    assert t.workorder_source_closed({"chrStatus": "Open"}) is False
    assert t.workorder_source_closed({"chrStatus": "???"}) is None
    assert t.workorder_source_closed({}) is None
