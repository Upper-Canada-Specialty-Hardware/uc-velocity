"""Unit tests for legacy-import helpers.

Issue #54: the legacy UC Vision WorkorderID (its quote-number) must be
prepended as "[WO {id}]" to the very start of a migrated quote's work
description. These tests exercise the pure prefixing helper directly,
without standing up the full /import integration path.
"""
from routes.migration import wo_prefixed_description


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
