"""Tests for the Projects page list view's per-project counts (#327).

Read-only: compares what /projects/list-view reports against the database's
own counts, so it works on whatever data the scratch database holds.
"""
import os

import pytest

if not os.getenv("DATABASE_URL"):
    pytest.skip("needs DATABASE_URL (CI, or a local scratch database)", allow_module_level=True)

from fastapi.testclient import TestClient
from sqlalchemy import func

from database import SessionLocal
from main import app
from models import Invoice, PurchaseOrder, Quote

client = TestClient(app)


def _counts(db, column, model):
    """project id -> number of rows of ``model`` grouped on ``column``."""
    return dict(db.query(column, func.count(model.id)).group_by(column).all())


def test_list_view_counts_match_the_database():
    """quote_count, po_count and invoice_count equal the database's counts for every project."""
    r = client.get("/projects/list-view")
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        quotes = _counts(db, Quote.project_id, Quote)               # quotes per project
        pos = _counts(db, PurchaseOrder.project_id, PurchaseOrder)  # POs per project
        invoices = dict(                                          # invoices per project, through their quote
            db.query(Quote.project_id, func.count(Invoice.id))
            .join(Invoice, Invoice.quote_id == Quote.id)
            .group_by(Quote.project_id)
            .all()
        )
    finally:
        db.close()
    for p in r.json():
        assert p["quote_count"] == quotes.get(p["id"], 0), p
        assert p["po_count"] == pos.get(p["id"], 0), p
        assert p["invoice_count"] == invoices.get(p["id"], 0), p   # 0 for a project with none, never missing


def test_search_rows_carry_invoice_count():
    """Search results use the same row builder, so they carry the invoice count too."""
    rows = client.get("/projects/list-view").json()
    if not rows:
        pytest.skip("no projects in this database")
    term = rows[0]["uca_project_number"]                          # a number that is certainly there
    hits = client.get("/projects/search", params={"q": term}).json()
    assert hits and all("invoice_count" in h for h in hits)
