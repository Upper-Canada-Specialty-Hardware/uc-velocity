"""End-to-end tests for fractional quantities on labour and misc quote lines.

Labour and misc lines may carry up to 2 decimals (e.g. 1.5 hours); parts stay
whole. These tests drive the real API against Postgres and cover:
- add line / update line / commit edits accept fractions for labour and misc,
  reject them for parts, and reject more than 2 decimals everywhere
- invoicing 0.7 then 0.8 of a 1.5 line leaves exactly 0 pending, derives
  "Closed" and drops the line from the backlog report; 0.1 then 0.2 of a
  0.3 line (where raw float maths leaves a tiny remainder) does the same
- a part can only be invoiced in whole units
- history (snapshots) stores the fraction verbatim, and revert/clone/reopen
  carry it in both directions, including a revert that voids an invoice
- invoice history returns fractions
- read endpoints still return stored rows (-1, 1.333) that input rules refuse
- audit text reads "2 → 3" for whole numbers and "2 → 1.5" for fractions
- the migration's downgrade refuses to run while any fraction is stored, and
  succeeds (inside a rolled-back transaction) when every value is whole

DB-dependent: skipped when Postgres is unreachable, like the other DB tests;
CI runs it against its Postgres. Everything created is deleted afterwards.
"""
import importlib.util
import socket
from datetime import datetime
from pathlib import Path


import pytest


def _pg_reachable():
    """Return True when Postgres is listening on localhost:5432."""
    try:
        socket.create_connection(("localhost", 5432), timeout=1).close()
        return True
    except OSError:
        return False


if not _pg_reachable():
    # Same rule as conftest's ignore list: no local Postgres -> nothing to test
    pytest.skip("Postgres not reachable on localhost:5432", allow_module_level=True)

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

import main
from database import SessionLocal, engine
from models import (
    Profile, ProfileType, Project, Quote, QuoteLineItem, QuoteLineItemSnapshot,
    QuoteSnapshot, Labor, Part, Invoice, InvoiceLineItem,
)

client = TestClient(main.app)

# Path to the migration under test, loaded by file (its name is not importable)
MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic" / "versions" / "20260928_033_fractional_quantities.py"
)


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
    """Create a customer, project, labour item and part; delete them all afterwards.

    Deleting the project cascades to every quote in it (including clones), and
    each quote cascades to its lines, invoices and snapshots.

    Yields:
        dict with ``project``, ``labor`` and ``part`` ORM rows.
    """
    s = _suffix()
    customer = Profile(name=f"[TEST] Frac Cust {s}", type=ProfileType.customer,
                       pst="PST-TEST", address="1 Test St", postal_code="A1A1A1")
    db.add(customer)
    db.flush()
    project = Project(name=f"[TEST] Frac Proj {s}", customer_id=customer.id,
                      uca_project_number=f"FRQ{s}")
    labor = Labor(description=f"[TEST] Frac labour {s}", hours=1, rate=100.0, markup_percent=0.0)
    part = Part(part_number=f"TEST-FRAC-{s}", description=f"[TEST] Frac part {s}",
                cost=10.0, markup_percent=0.0)
    db.add_all([project, labor, part])
    db.commit()
    ids = {"customer": customer.id, "project": project.id, "labor": labor.id, "part": part.id}
    try:
        yield {"project": project, "labor": labor, "part": part}
    finally:
        # Fresh session: the test may have left the fixture session in any state
        cleanup = SessionLocal()
        try:
            proj = cleanup.get(Project, ids["project"])
            if proj is not None:
                cleanup.delete(proj)  # cascades quotes -> lines/invoices/snapshots
            cleanup.flush()
            for model, key in ((Labor, "labor"), (Part, "part"), (Profile, "customer")):
                row = cleanup.get(model, ids[key])
                if row is not None:
                    cleanup.delete(row)
            cleanup.commit()
        finally:
            cleanup.close()


def _make_quote(db, env, *, client_po="PO-TEST", legacy=False):
    """Create an empty quote in the fixture project.

    Args:
        db: ORM session.
        env: The ``env`` fixture dict.
        client_po: Client PO number (required before invoicing).
        legacy: Mark the quote as migrated, which the reopen endpoint requires.

    Returns:
        The new quote's id.
    """
    project_id = env["project"].id
    seq = (db.query(Quote).filter(Quote.project_id == project_id).count()) + 1
    quote = Quote(project_id=project_id, quote_sequence=seq, current_version=0,
                  client_po_number=client_po, markup_control_enabled=False,
                  legacy_imported=legacy)
    db.add(quote)
    db.commit()
    return quote.id


def _add_line(quote_id, **body):
    """POST a new line to a quote and return the raw response."""
    return client.post(f"/quotes/{quote_id}/lines", json=body)


def _add_labour(quote_id, env, qty):
    """Add a labour line with ``qty`` and return the response JSON (asserts 200)."""
    r = _add_line(quote_id, item_type="labor", labor_id=env["labor"].id, quantity=qty)
    assert r.status_code == 200, r.text
    return r.json()


def _add_misc(quote_id, qty):
    """Add a free-text misc line with ``qty`` and return the response JSON (asserts 200)."""
    r = _add_line(quote_id, item_type="misc", description="[TEST] misc", unit_price=10.0, quantity=qty)
    assert r.status_code == 200, r.text
    return r.json()


def _invoice(quote_id, line_id, qty):
    """Invoice ``qty`` of one line and return the raw response."""
    return client.post(f"/quotes/{quote_id}/invoices",
                       json={"fulfillments": [{"line_item_id": line_id, "quantity": qty}]})


def _quote(quote_id):
    """GET a quote and return its JSON (asserts 200)."""
    r = client.get(f"/quotes/{quote_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _line(quote_id, line_id=None):
    """Return one line of a quote (the only one when ``line_id`` is None)."""
    lines = _quote(quote_id)["line_items"]
    if line_id is None:
        assert len(lines) == 1
        return lines[0]
    return next(li for li in lines if li["id"] == line_id)


def _snapshots(quote_id):
    """Return the quote's history keyed by version."""
    r = client.get(f"/quotes/{quote_id}/snapshots")
    assert r.status_code == 200, r.text
    return {s["version"]: s for s in r.json()}


# ---------------------------------------------------------------- accept / reject


def test_add_line_accepts_fractions_for_labour_and_misc(db, env):
    """Labour and misc lines store the fraction and start fully pending."""
    quote_id = _make_quote(db, env)
    labour = _add_labour(quote_id, env, 1.5)
    assert labour["quantity"] == 1.5 and labour["qty_pending"] == 1.5
    misc = _add_misc(quote_id, 0.25)
    assert misc["quantity"] == 0.25 and misc["qty_pending"] == 0.25


def test_add_line_rejects_fractional_part(db, env):
    """A part must be whole; a whole part quantity still works."""
    quote_id = _make_quote(db, env)
    r = _add_line(quote_id, item_type="part", part_id=env["part"].id, quantity=1.5)
    assert r.status_code == 400
    assert "whole numbers" in r.json()["detail"]
    r = _add_line(quote_id, item_type="part", part_id=env["part"].id, quantity=2)
    assert r.status_code == 200, r.text
    assert r.json()["quantity"] == 2


def test_more_than_two_decimals_rejected_everywhere(db, env):
    """1.234 is refused by add, update, commit edits and invoicing."""
    quote_id = _make_quote(db, env)
    line = _add_labour(quote_id, env, 1)
    assert _add_line(quote_id, item_type="labor", labor_id=env["labor"].id,
                     quantity=1.234).status_code == 422
    assert client.put(f"/quotes/{quote_id}/lines/{line['id']}",
                      json={"quantity": 1.234}).status_code == 422
    r = client.post(f"/quotes/{quote_id}/commit", json={"changes": [
        {"action": "add", "item_type": "misc", "description": "[TEST] x", "quantity": 1.234}]})
    assert r.status_code == 422
    assert _invoice(quote_id, line["id"], 0.123).status_code == 422
    assert _line(quote_id)["quantity"] == 1  # nothing changed


def test_update_line_fraction_for_labour_not_part(db, env):
    """Update accepts 2.5 on labour (pending follows) and refuses 1.5 on a part."""
    quote_id = _make_quote(db, env)
    labour = _add_labour(quote_id, env, 1)
    r = client.put(f"/quotes/{quote_id}/lines/{labour['id']}", json={"quantity": 2.5})
    assert r.status_code == 200, r.text
    assert r.json()["quantity"] == 2.5 and r.json()["qty_pending"] == 2.5

    part = _add_line(quote_id, item_type="part", part_id=env["part"].id, quantity=1).json()
    r = client.put(f"/quotes/{quote_id}/lines/{part['id']}", json={"quantity": 1.5})
    assert r.status_code == 400
    assert "whole numbers" in r.json()["detail"]
    r = client.put(f"/quotes/{quote_id}/lines/{part['id']}", json={"quantity": 3})
    assert r.status_code == 200, r.text


def test_commit_edits_fractions(db, env):
    """Commit edits: fractional add/edit for labour/misc; parts refused on add and edit."""
    quote_id = _make_quote(db, env)
    labour = _add_labour(quote_id, env, 1)
    part = _add_line(quote_id, item_type="part", part_id=env["part"].id, quantity=1).json()

    r = client.post(f"/quotes/{quote_id}/commit", json={"changes": [
        {"action": "add", "item_type": "misc", "description": "[TEST] m", "unit_price": 5.0,
         "quantity": 1.75},
        {"action": "edit", "line_item_id": labour["id"], "quantity": 0.5},
    ]})
    assert r.status_code == 200, r.text
    lines = {li["item_type"]: li for li in _quote(quote_id)["line_items"]}
    assert lines["misc"]["quantity"] == 1.75 and lines["misc"]["qty_pending"] == 1.75
    assert lines["labor"]["quantity"] == 0.5 and lines["labor"]["qty_pending"] == 0.5

    # Adding a fractional part is refused
    r = client.post(f"/quotes/{quote_id}/commit", json={"changes": [
        {"action": "add", "item_type": "part", "part_id": env["part"].id, "quantity": 1.5}]})
    assert r.status_code == 400
    # Editing a part is refused even if the request claims a different item_type:
    # the stored line's type decides
    r = client.post(f"/quotes/{quote_id}/commit", json={"changes": [
        {"action": "edit", "line_item_id": part["id"], "item_type": "labor", "quantity": 2.5}]})
    assert r.status_code == 400
    assert "whole numbers" in r.json()["detail"]
    assert _line(quote_id, part["id"])["quantity"] == 1  # untouched


# ---------------------------------------------------------------- invoicing


def test_fractional_invoicing_closes_quote_and_clears_backlog(db, env):
    """0.7 then 0.8 of a 1.5 misc line: pending exactly 0, Closed, gone from backlog."""
    quote_id = _make_quote(db, env)
    line = _add_misc(quote_id, 1.5)

    r = _invoice(quote_id, line["id"], 0.7)
    assert r.status_code == 200, r.text
    inv_line = r.json()["line_items"][0]
    assert inv_line["qty_fulfilled_this_invoice"] == 0.7
    assert inv_line["qty_pending_after"] == 0.8
    assert _quote(quote_id)["status"] == "Invoiced"
    backlog = client.get("/reports/backlog-quotes").json()
    ours = [q for q in backlog if q["quote_id"] == quote_id]
    assert len(ours) == 1 and ours[0]["line_items"][0]["qty_pending"] == 0.8

    r = _invoice(quote_id, line["id"], 0.8)
    assert r.status_code == 200, r.text
    inv_line = r.json()["line_items"][0]
    assert inv_line["qty_fulfilled_total"] == 1.5 and inv_line["qty_pending_after"] == 0.0

    db.expire_all()
    stored = db.get(QuoteLineItem, line["id"])
    assert stored.qty_pending == 0.0  # exact, not a float remainder
    assert stored.qty_fulfilled == 1.5
    assert _quote(quote_id)["status"] == "Closed"
    backlog = client.get("/reports/backlog-quotes").json()
    assert all(q["quote_id"] != quote_id for q in backlog)

    # Nothing left to invoice
    assert _invoice(quote_id, line["id"], 0.01).status_code == 400


def test_float_noise_never_leaves_a_pending_remainder(db, env):
    """0.1 then 0.2 of a 0.3 line: raw float maths gives -2.7e-17, stored pending is 0."""
    quote_id = _make_quote(db, env)
    line = _add_misc(quote_id, 0.3)
    assert _invoice(quote_id, line["id"], 0.1).status_code == 200
    # 0.3 - 0.1 is 0.19999999999999998 unrounded; 0.2 must still be accepted
    r = _invoice(quote_id, line["id"], 0.2)
    assert r.status_code == 200, r.text
    db.expire_all()
    stored = db.get(QuoteLineItem, line["id"])
    assert stored.qty_pending == 0.0 and stored.qty_fulfilled == 0.3
    assert _quote(quote_id)["status"] == "Closed"


def test_fractional_part_fulfillment_rejected(db, env):
    """A part line can only be invoiced in whole units."""
    quote_id = _make_quote(db, env)
    part = _add_line(quote_id, item_type="part", part_id=env["part"].id, quantity=3).json()
    r = _invoice(quote_id, part["id"], 1.5)
    assert r.status_code == 400
    assert "whole numbers" in r.json()["detail"]
    assert _invoice(quote_id, part["id"], 1).status_code == 200


def test_invoice_snapshot_returns_fractions(db, env):
    """Invoice detail and invoice history both return the fractional values."""
    quote_id = _make_quote(db, env)
    line = _add_misc(quote_id, 1.5)
    invoice_id = _invoice(quote_id, line["id"], 0.7).json()["id"]

    detail = client.get(f"/invoices/{invoice_id}").json()["line_items"][0]
    assert detail["qty_ordered"] == 1.5 and detail["qty_fulfilled_this_invoice"] == 0.7

    # A date edit writes an invoice snapshot of the current lines
    r = client.put(f"/invoices/{invoice_id}/created-at", json={"created_at": "2026-01-02T12:00:00"})
    assert r.status_code == 200, r.text
    snaps = client.get(f"/invoices/{invoice_id}/snapshots").json()
    state = snaps[0]["line_item_states"][0]
    assert state["qty_ordered"] == 1.5
    assert state["qty_fulfilled_this_invoice"] == 0.7
    assert state["qty_fulfilled_total"] == 0.7
    assert state["qty_pending_after"] == 0.8


# ---------------------------------------------------------------- history / revert / clone / reopen


def test_history_and_revert_round_trip_mixed_whole_and_fraction(db, env):
    """History keeps 2 and 1.5 verbatim; revert restores each in both directions."""
    quote_id = _make_quote(db, env)
    line = _add_labour(quote_id, env, 2)  # version 1: whole
    r = client.post(f"/quotes/{quote_id}/commit", json={"changes": [
        {"action": "edit", "line_item_id": line["id"], "quantity": 1.5}]})  # version 2: fraction
    assert r.status_code == 200, r.text

    snaps = _snapshots(quote_id)
    assert snaps[1]["line_item_states"][0]["quantity"] == 2
    assert snaps[2]["line_item_states"][0]["quantity"] == 1.5
    assert "qty: 2 → 1.5" in snaps[2]["action_description"]

    # Stored snapshot row keeps the exact value (not rounded to a whole number)
    db.expire_all()
    row = (db.query(QuoteLineItemSnapshot).join(QuoteSnapshot)
           .filter(QuoteSnapshot.quote_id == quote_id, QuoteSnapshot.version == 2).one())
    assert row.quantity == 1.5 and row.qty_pending == 1.5

    # Back to the whole-number version...
    assert client.post(f"/quotes/{quote_id}/revert/1").status_code == 200  # version 3
    restored = _line(quote_id)
    assert restored["quantity"] == 2 and restored["qty_pending"] == 2
    # ...and forward again to the fractional one
    assert client.post(f"/quotes/{quote_id}/revert/2").status_code == 200  # version 4
    restored = _line(quote_id)
    assert restored["quantity"] == 1.5 and restored["qty_pending"] == 1.5
    assert _snapshots(quote_id)[4]["line_item_states"][0]["quantity"] == 1.5


def test_revert_that_voids_invoice_restores_pending_and_fulfilled(db, env):
    """Reverting past a fractional invoice voids it and restores consistent totals."""
    quote_id = _make_quote(db, env)
    line = _add_misc(quote_id, 1.5)  # version 1
    invoice_id = _invoice(quote_id, line["id"], 0.7).json()["id"]  # version 2

    assert client.post(f"/quotes/{quote_id}/revert/1").status_code == 200  # version 3
    db.expire_all()
    assert db.get(Invoice, invoice_id).status == "Voided"
    restored = _line(quote_id)
    assert restored["qty_pending"] == 1.5 and restored["qty_fulfilled"] == 0
    assert _quote(quote_id)["status"] == "Work Order"

    # Revert to the invoiced state brings back the fractional split
    assert client.post(f"/quotes/{quote_id}/revert/2").status_code == 200
    restored = _line(quote_id)
    assert restored["qty_pending"] == 0.8 and restored["qty_fulfilled"] == 0.7
    assert restored["qty_pending"] + restored["qty_fulfilled"] == restored["quantity"]


def test_clone_keeps_fraction(db, env):
    """A clone copies 1.5 and resets it to fully pending."""
    quote_id = _make_quote(db, env)
    line = _add_misc(quote_id, 1.5)
    assert _invoice(quote_id, line["id"], 0.7).status_code == 200
    r = client.post(f"/quotes/{quote_id}/clone")
    assert r.status_code == 200, r.text
    cloned = r.json()["line_items"][0]
    assert cloned["quantity"] == 1.5
    assert cloned["qty_pending"] == 1.5 and cloned["qty_fulfilled"] == 0


def test_reopen_keeps_fraction(db, env):
    """Reopening a closed migrated quote puts the whole 1.5 back to pending."""
    quote_id = _make_quote(db, env, legacy=True)
    db.add(QuoteLineItem(quote_id=quote_id, item_type="misc", description="[TEST] legacy",
                         quantity=1.5, unit_price=10.0, qty_pending=0, qty_fulfilled=1.5))
    db.commit()
    assert _quote(quote_id)["status"] == "Closed"

    r = client.post(f"/quotes/{quote_id}/reopen")
    assert r.status_code == 200, r.text
    reopened = _line(quote_id)
    assert reopened["quantity"] == 1.5
    assert reopened["qty_pending"] == 1.5 and reopened["qty_fulfilled"] == 0


def test_reads_return_stored_rows_that_input_rules_would_refuse(db, env):
    """History and invoice GETs still return a -1 and a 1.333 already stored.

    Response schemas validate rows already in the database (including legacy
    imports), so they must pass values through rather than 500 the page.
    """
    quote_id = _make_quote(db, env)
    snap = QuoteSnapshot(quote_id=quote_id, version=1, action_type="edit")
    db.add(snap)
    db.flush()
    # Inserted directly: the API would never write these values
    db.add(QuoteLineItemSnapshot(snapshot_id=snap.id, item_type="misc", description="[TEST] odd",
                                 quantity=1.333, qty_pending=-1, qty_fulfilled=1.333,
                                 is_deleted=False))
    invoice = Invoice(quote_id=quote_id, status="Sent", invoice_sequence=1)
    db.add(invoice)
    db.flush()
    db.add(InvoiceLineItem(invoice_id=invoice.id, item_type="misc", description="[TEST] odd",
                           unit_price=1.0, qty_ordered=1.333, qty_fulfilled_this_invoice=1.333,
                           qty_fulfilled_total=1.333, qty_pending_after=-1))
    db.commit()
    invoice_id = invoice.id

    r = client.get(f"/quotes/{quote_id}/snapshots")
    assert r.status_code == 200, r.text
    state = r.json()[0]["line_item_states"][0]
    assert state["quantity"] == 1.333 and state["qty_pending"] == -1

    for url in (f"/invoices/{invoice_id}", f"/quotes/{quote_id}/invoices"):
        r = client.get(url)
        assert r.status_code == 200, r.text
        body = r.json()
        line = (body if isinstance(body, dict) else body[0])["line_items"][0]
        assert line["qty_ordered"] == 1.333 and line["qty_pending_after"] == -1
    # Rows are removed by the env fixture (project -> quote -> snapshot/invoice cascade)


# ---------------------------------------------------------------- audit text


def test_audit_descriptions_whole_and_fraction(db, env):
    """Whole numbers read "2 → 3" (no ".0"); fractions read "1 → 1.5"."""
    quote_id = _make_quote(db, env)
    line = _add_labour(quote_id, env, 2)
    assert client.post(f"/quotes/{quote_id}/commit", json={"changes": [
        {"action": "edit", "line_item_id": line["id"], "quantity": 3}]}).status_code == 200
    other = _add_misc(quote_id, 1)
    assert client.post(f"/quotes/{quote_id}/commit", json={"changes": [
        {"action": "edit", "line_item_id": other["id"], "quantity": 1.5}]}).status_code == 200
    r = client.put(f"/quotes/{quote_id}/lines/{other['id']}", json={"quantity": 2})
    assert r.status_code == 200, r.text

    descriptions = [s["action_description"] for s in _snapshots(quote_id).values()]
    assert any("(qty: 2)" in d for d in descriptions)  # add line
    assert any("qty: 2 → 3" in d for d in descriptions)  # commit edit, whole
    assert any("qty: 1 → 1.5" in d for d in descriptions)  # commit edit, fraction
    assert any("quantity: 1.5 → 2" in d for d in descriptions)  # single-line update
    assert not any(".0" in d for d in descriptions)  # never "2.0"


# ---------------------------------------------------------------- migration downgrade


def _load_migration():
    """Import the fractional-quantities migration module from its file path."""
    spec = importlib.util.spec_from_file_location("fractional_qty_migration", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _column_type(conn, table, column):
    """Return Postgres' data_type for one column (e.g. 'double precision')."""
    return conn.execute(text(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = :t AND column_name = :c"), {"t": table, "c": column}).scalar_one()


def test_migration_downgrade_refuses_while_fractions_exist():
    """The real downgrade() raises and changes nothing when a fraction is stored.

    Runs inside one transaction that is always rolled back, so neither the
    temporary rows nor any type change survive the test.
    """
    migration = _load_migration()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # Temporary fractional quote line, visible only inside this transaction
            session = Session(bind=conn)
            s = _suffix()
            customer = Profile(name=f"[TEST] Mig Cust {s}", type=ProfileType.customer,
                               pst="PST-TEST", address="1 Test St", postal_code="A1A1A1")
            session.add(customer)
            session.flush()
            project = Project(name=f"[TEST] Mig Proj {s}", customer_id=customer.id,
                              uca_project_number=f"MIG{s}")
            session.add(project)
            session.flush()
            quote = Quote(project_id=project.id, quote_sequence=1, current_version=0)
            session.add(quote)
            session.flush()
            line = QuoteLineItem(quote_id=quote.id, item_type="misc", description="[TEST] mig",
                                 quantity=1.5, qty_pending=1.5, qty_fulfilled=0)
            session.add(line)
            session.flush()
            line_id = line.id

            assert migration.count_fractional_rows(conn)["quote_line_items"] >= 1
            with Operations.context(MigrationContext.configure(conn)):
                with pytest.raises(RuntimeError, match="Fix forward instead"):
                    migration.downgrade()

            # Nothing changed: type and value are exactly as before
            assert _column_type(conn, "quote_line_items", "quantity") == "double precision"
            assert conn.execute(text("SELECT quantity FROM quote_line_items WHERE id = :i"),
                                {"i": line_id}).scalar_one() == 1.5
            session.close()
        finally:
            trans.rollback()  # discard the temporary rows


def test_migration_downgrade_succeeds_when_all_whole():
    """With only whole values, downgrade() converts all 14 columns back to integer.

    Runs inside a rolled-back transaction, so the schema is restored afterwards.
    Skips if the database already holds real fractional quantities (the refusal
    path is covered by the test above).
    """
    migration = _load_migration()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            if any(migration.count_fractional_rows(conn).values()):
                pytest.skip("database already holds fractional quantities")
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
            for table, columns in migration.QUANTITY_COLUMNS.items():
                for col in columns:
                    assert _column_type(conn, table, col) == "integer", (table, col)
        finally:
            trans.rollback()  # undo the type change; the DB stays at head
