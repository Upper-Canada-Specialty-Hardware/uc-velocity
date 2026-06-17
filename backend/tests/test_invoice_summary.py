"""Tests for the invoice summary report endpoint."""
from datetime import date

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# A wide date range that should capture all existing test data
WIDE_START = "2000-01-01"
WIDE_END = "2099-12-31"


def test_invoice_summary_returns_200():
    """GET /invoices/ with a date range returns 200 with a list."""
    r = client.get(f"/invoices/?start_date={WIDE_START}&end_date={WIDE_END}")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_invoice_summary_rows_carry_project_fields():
    """Every row must have UCA project number, project name, and customer name."""
    r = client.get(f"/invoices/?start_date={WIDE_START}&end_date={WIDE_END}")
    assert r.status_code == 200
    for item in r.json():
        assert item["uca_project_number"]
        assert item["project_name"]
        assert item["customer_name"]


def test_invoice_summary_customer_and_project_are_distinct_fields():
    """customer_name and project_name must come through as separate string fields."""
    r = client.get(f"/invoices/?start_date={WIDE_START}&end_date={WIDE_END}")
    assert r.status_code == 200
    for item in r.json():
        assert "customer_name" in item
        assert "project_name" in item
        assert isinstance(item["customer_name"], str)
        assert isinstance(item["project_name"], str)


def test_invoice_summary_sorted_by_date_then_customer_then_project():
    """Rows must be ordered by invoice date, then customer name, then project name."""
    r = client.get(f"/invoices/?start_date={WIDE_START}&end_date={WIDE_END}")
    assert r.status_code == 200
    rows = r.json()

    # The sort key mirrors the backend order_by: created_at, then customer, then project.
    # Casefold the text tie-breakers so the oracle is collation-agnostic: Python's
    # code-point ordering and Postgres' locale-aware collation disagree on raw case,
    # but both put names in the same case-insensitive order.
    keys = [
        (row["invoice_date"], row["customer_name"].casefold(), row["project_name"].casefold())
        for row in rows
    ]
    assert keys == sorted(keys), "Rows are not sorted by date, then customer, then project"


def test_invoice_summary_project_filter_narrows_results():
    """Passing project_id must return only invoices for that project."""
    all_rows = client.get(
        f"/invoices/?start_date={WIDE_START}&end_date={WIDE_END}"
    ).json()
    if not all_rows:
        return  # nothing to test against — environment has no invoices

    # Pick a project that actually has an invoice
    first = all_rows[0]
    # The summary doesn't expose project_id directly, so we look it up via /projects/list-view
    projects = client.get("/projects/list-view").json()
    target = next(
        (p for p in projects if p["uca_project_number"] == first["uca_project_number"]),
        None,
    )
    assert target is not None, "Could not resolve project_id for first invoice"

    filtered = client.get(
        f"/invoices/?start_date={WIDE_START}&end_date={WIDE_END}&project_id={target['id']}"
    ).json()

    assert len(filtered) <= len(all_rows)
    assert len(filtered) > 0, "Expected at least one row for the picked project"
    for row in filtered:
        assert row["uca_project_number"] == target["uca_project_number"]


def test_invoice_summary_unknown_project_returns_empty():
    """An invalid project_id should return an empty list, not an error."""
    r = client.get(
        f"/invoices/?start_date={WIDE_START}&end_date={WIDE_END}&project_id=999999999"
    )
    assert r.status_code == 200
    assert r.json() == []
