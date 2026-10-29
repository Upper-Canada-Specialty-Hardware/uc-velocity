"""End-to-end tests for negative quantities (credits) on quote lines (issue #277).

A quote line may carry a NEGATIVE quantity: a credit, pending until invoiced and
invoiced toward zero (e.g. -5 invoiced -2 then -3). Zero is still refused. Parts
stay whole (credits too); labour and misc may be fractional. PO lines are not
affected. An invoice's own total can never be below $0.

These tests drive the real API against Postgres and cover:
- a credit line is accepted and starts fully pending; it shows in the backlog
- zero is refused on add and on invoicing
- a part credit must be whole; a whole part credit is accepted
- a credit invoiced in two parts toward zero, alongside positive lines, closes the quote
- an invoice whose total would be below $0 is refused and changes nothing; a $0 total is allowed
- a pending credit keeps the quote open, and invoicing a credit freezes the quote
- a credit cannot be over-invoiced, and the fulfilment sign must match the pending

DB-dependent: skipped when the database in DATABASE_URL is unreachable (so a plain
local `pytest` with no DB does not error at import); CI and the scratch DB run it.
Everything created is deleted afterwards by the env fixture's cascade.
"""
import os
import socket
from datetime import datetime
from urllib.parse import urlparse

import pytest


def _db_reachable():
    """Return True when the database named in DATABASE_URL accepts a TCP connection.

    Unlike conftest's fixed :5432 probe, this follows DATABASE_URL so the scratch
    DB on :5433 (and CI's own Postgres) are both detected.

    Returns:
        True when DATABASE_URL is set and its host:port is reachable, else False.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False  # no DB configured -> nothing to test, and importing main would fail
    parsed = urlparse(url)
    host, port = parsed.hostname or "localhost", parsed.port or 5432
    try:
        socket.create_connection((host, port), timeout=1).close()
        return True
    except OSError:
        return False


if not _db_reachable():
    # Skip the whole module before importing main (which needs DATABASE_URL + a live DB)
    pytest.skip("DATABASE_URL unset or database unreachable", allow_module_level=True)

from fastapi.testclient import TestClient

import main
from database import SessionLocal
from models import Part, Profile, ProfileType, Project, Quote, QuoteLineItem

client = TestClient(main.app)


def _suffix():
    """Unique-ish suffix so fixture rows never collide with other data."""
    return datetime.utcnow().strftime("%H%M%S%f")


@pytest.fixture
def db():
    """Plain ORM session for direct setup/inspection."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def env(db):
    """Create a customer, project and part; delete them all afterwards.

    Deleting the project cascades to every quote in it, and each quote cascades
    to its lines, invoices and snapshots.

    Yields:
        dict with ``project`` and ``part`` ORM rows.
    """
    s = _suffix()
    customer = Profile(name=f"[TEST] Credit Cust {s}", type=ProfileType.customer,
                       pst="PST-TEST", address="1 Test St", postal_code="A1A1A1")
    db.add(customer)
    db.flush()
    project = Project(name=f"[TEST] Credit Proj {s}", customer_id=customer.id,
                      uca_project_number=f"CRD{s}")
    part = Part(part_number=f"TEST-CRD-{s}", description=f"[TEST] Credit part {s}",
                cost=10.0, markup_percent=0.0)
    db.add_all([project, part])
    db.commit()
    ids = {"customer": customer.id, "project": project.id, "part": part.id}
    try:
        yield {"project": project, "part": part}
    finally:
        cleanup = SessionLocal()
        try:
            proj = cleanup.get(Project, ids["project"])
            if proj is not None:
                cleanup.delete(proj)  # cascades quotes -> lines/invoices/snapshots
            cleanup.flush()
            for key, model in (("part", Part), ("customer", Profile)):
                row = cleanup.get(model, ids[key])
                if row is not None:
                    cleanup.delete(row)
            cleanup.commit()
        finally:
            cleanup.close()


def _make_quote(db, env, *, client_po="PO-TEST"):
    """Create an empty quote in the fixture project and return its id."""
    project_id = env["project"].id
    seq = (db.query(Quote).filter(Quote.project_id == project_id).count()) + 1
    quote = Quote(project_id=project_id, quote_sequence=seq, current_version=0,
                  client_po_number=client_po, markup_control_enabled=False)
    db.add(quote)
    db.commit()
    return quote.id


def _add_line(quote_id, **body):
    """POST a new line to a quote and return the raw response."""
    return client.post(f"/quotes/{quote_id}/lines", json=body)


def _add_misc(quote_id, qty, unit_price=10.0):
    """Add a free-text misc line (unit_price becomes its base) and return response JSON (asserts 200)."""
    r = _add_line(quote_id, item_type="misc", description="[TEST] misc", unit_price=unit_price, quantity=qty)
    assert r.status_code == 200, r.text
    return r.json()


def _invoice(quote_id, fulfillments):
    """Invoice one or more lines; ``fulfillments`` is a list of (line_id, qty). Raw response."""
    body = {"fulfillments": [{"line_item_id": lid, "quantity": q} for lid, q in fulfillments]}
    return client.post(f"/quotes/{quote_id}/invoices", json=body)


def _quote(quote_id):
    """GET a quote and return its JSON (asserts 200)."""
    r = client.get(f"/quotes/{quote_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _line(quote_id, line_id):
    """Return one line of a quote by id."""
    return next(li for li in _quote(quote_id)["line_items"] if li["id"] == line_id)


# ---------------------------------------------------------------- accept / reject


def test_credit_line_accepted_and_shows_in_backlog(db, env):
    """A negative misc line stores as a credit, starts fully pending, and backlogs."""
    quote_id = _make_quote(db, env)
    credit = _add_misc(quote_id, -5, unit_price=10.0)
    assert credit["quantity"] == -5
    assert credit["qty_pending"] == -5 and credit["qty_fulfilled"] == 0

    # Nothing fulfilled yet, has a client PO -> an open Work Order
    assert _quote(quote_id)["status"] == "Work Order"

    # The pending credit appears in the backlog, reducing the backlog value by its amount
    backlog = client.get("/reports/backlog-quotes").json()
    ours = [q for q in backlog if q["quote_id"] == quote_id]
    assert len(ours) == 1
    line = ours[0]["line_items"][0]
    assert line["qty_pending"] == -5 and line["backlog_value"] == -50.0


def test_zero_quantity_refused(db, env):
    """Zero is refused both when ordering a line and when invoicing."""
    quote_id = _make_quote(db, env)
    assert _add_line(quote_id, item_type="misc", description="[TEST] z",
                     unit_price=10.0, quantity=0).status_code == 422
    line = _add_misc(quote_id, 3)
    assert _invoice(quote_id, [(line["id"], 0)]).status_code == 422


def test_part_credit_must_be_whole(db, env):
    """A part credit must be a whole number; a whole credit is accepted."""
    quote_id = _make_quote(db, env)
    r = _add_line(quote_id, item_type="part", part_id=env["part"].id, quantity=-1.5)
    assert r.status_code == 400 and "whole numbers" in r.json()["detail"]
    r = _add_line(quote_id, item_type="part", part_id=env["part"].id, quantity=-2)
    assert r.status_code == 200, r.text
    assert r.json()["quantity"] == -2 and r.json()["qty_pending"] == -2


# ---------------------------------------------------------------- invoicing a credit


def test_credit_invoiced_in_two_parts_closes_quote(db, env):
    """A -5 credit invoiced -2 then -3, riding positive lines, lands pending 0 and Closed."""
    quote_id = _make_quote(db, env)
    positive = _add_misc(quote_id, 8, unit_price=10.0)   # $80 of capacity
    credit = _add_misc(quote_id, -5, unit_price=10.0)    # -$50 credit

    # Invoice 1: +3 of the positive and -2 of the credit -> net 30 - 20 = 10 (>= 0)
    r = _invoice(quote_id, [(positive["id"], 3), (credit["id"], -2)])
    assert r.status_code == 200, r.text
    assert _line(quote_id, credit["id"])["qty_pending"] == -3
    assert _quote(quote_id)["status"] == "Invoiced"  # still something pending

    # Invoice 2: +5 and -3 -> net 50 - 30 = 20; both lines land on exactly 0 pending
    r = _invoice(quote_id, [(positive["id"], 5), (credit["id"], -3)])
    assert r.status_code == 200, r.text

    db.expire_all()
    stored = db.get(QuoteLineItem, credit["id"])
    assert stored.qty_fulfilled == -5 and stored.qty_pending == 0  # exact, not float noise
    pos = db.get(QuoteLineItem, positive["id"])
    assert pos.qty_fulfilled == 8 and pos.qty_pending == 0
    assert _quote(quote_id)["status"] == "Closed"


def test_invoice_below_zero_refused_zero_allowed(db, env):
    """An invoice total below $0 is refused and changes nothing; an exactly-$0 total is allowed."""
    quote_id = _make_quote(db, env)
    positive = _add_misc(quote_id, 1, unit_price=10.0)
    credit = _add_misc(quote_id, -5, unit_price=10.0)

    # Credit alone: net -20 -> refused, with a clear message, nothing changed
    r = _invoice(quote_id, [(credit["id"], -2)])
    assert r.status_code == 400 and "below $0" in r.json()["detail"]
    assert _line(quote_id, credit["id"])["qty_pending"] == -5  # untouched

    # Mixed but still negative: 10 - 20 = -10 -> refused
    assert _invoice(quote_id, [(positive["id"], 1), (credit["id"], -2)]).status_code == 400

    # Exactly $0 (10 - 10) is allowed
    r = _invoice(quote_id, [(positive["id"], 1), (credit["id"], -1)])
    assert r.status_code == 200, r.text
    assert _line(quote_id, credit["id"])["qty_pending"] == -4


def test_pending_credit_keeps_quote_open_and_freezes_it(db, env):
    """Invoicing the positives fully but only part of the credit leaves the quote open and frozen."""
    quote_id = _make_quote(db, env)
    positive = _add_misc(quote_id, 10, unit_price=10.0)
    credit = _add_misc(quote_id, -5, unit_price=10.0)

    # +10 (full positive) and -2 of the credit -> net 100 - 20 = 80
    r = _invoice(quote_id, [(positive["id"], 10), (credit["id"], -2)])
    assert r.status_code == 200, r.text
    # Credit still pending -> quote is Invoiced (open), not Closed
    assert _line(quote_id, credit["id"])["qty_pending"] == -3
    assert _quote(quote_id)["status"] == "Invoiced"

    # The quote is now frozen (a line was fulfilled): editing a line is refused
    r = client.put(f"/quotes/{quote_id}/lines/{credit['id']}", json={"quantity": -4})
    assert r.status_code == 400 and "frozen" in r.json()["detail"]


def test_credit_cannot_be_over_invoiced_or_wrong_sign(db, env):
    """A credit's amount must be negative and within its pending magnitude."""
    quote_id = _make_quote(db, env)
    positive = _add_misc(quote_id, 100, unit_price=10.0)  # plenty of positive cover
    credit = _add_misc(quote_id, -5, unit_price=10.0)

    # -6 exceeds the -5 pending
    assert _invoice(quote_id, [(positive["id"], 100), (credit["id"], -6)]).status_code == 400
    # a positive amount on a credit line is the wrong side of zero
    assert _invoice(quote_id, [(positive["id"], 1), (credit["id"], 2)]).status_code == 400
    # the credit is untouched by the refusals
    assert _line(quote_id, credit["id"])["qty_pending"] == -5
