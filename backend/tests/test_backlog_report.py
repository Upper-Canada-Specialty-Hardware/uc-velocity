"""Tests for the backlog quotes report endpoint."""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_backlog_report_returns_200():
    """GET /reports/backlog-quotes should return 200 with a list."""
    r = client.get("/reports/backlog-quotes")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)


def test_backlog_report_excludes_draft_and_closed():
    """Every returned quote must be Work Order or Invoiced."""
    r = client.get("/reports/backlog-quotes")
    assert r.status_code == 200
    for item in r.json():
        assert item["status"] in ("Work Order", "Invoiced"), (
            f"Unexpected status {item['status']} for quote {item['quote_number']}"
        )


def test_backlog_report_items_have_pending():
    """Every returned line item must still have something pending.

    Non-zero rather than "> 0": a credit line (issue #277) is uninvoiced until it
    reaches zero, so a pending credit (negative qty_pending) legitimately backlogs.
    Only a fully-settled line (qty_pending == 0) is excluded.
    """
    r = client.get("/reports/backlog-quotes")
    assert r.status_code == 200
    for quote in r.json():
        for li in quote["line_items"]:
            assert li["qty_pending"] != 0, (
                f"Line item {li['line_item_id']} in {quote['quote_number']} has qty_pending=0"
            )
