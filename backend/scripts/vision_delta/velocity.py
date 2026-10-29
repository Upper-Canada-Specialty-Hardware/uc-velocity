"""Read the records the compare needs out of a Velocity database, read-only.

The connection is opened with ``default_transaction_read_only=on``, so Postgres
itself refuses any write on it; that makes it safe to point at production.
Records come back in the same shapes as :mod:`scripts.vision_delta.vision`,
plus Velocity's own ids, versions and last-change dates.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import create_engine, text

from routes.quotes import compute_status_from_lines

# The "[WO n]" tag the import puts at the start of a migrated quote's description
# Snapshot actor of the cutover tool (apply.py's ACTOR): its own versions are not staff edits
TOOL_ACTOR = "vision-delta-tool"
WO_TAG = re.compile(r"^\[WO (\d+)\]")


def connect(url: str):
    """Open a read-only connection.

    Args:
        url: The Velocity Postgres URL.

    Returns:
        A SQLAlchemy connection whose every transaction is read-only.
    """
    engine = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on"})
    conn = engine.connect()
    conn.execute(text("SELECT 1"))                         # fail fast on a bad URL
    return conn


def _rows(conn, sql: str, params: Optional[dict] = None) -> list[dict]:
    """Run a query (with optional bound parameters) and return its rows as dicts."""
    return [dict(r._mapping) for r in conn.execute(text(sql), params or {})]


def wo_of(description: Optional[str]) -> Optional[int]:
    """The Vision work-order number in a quote's "[WO n]" tag, if any."""
    m = WO_TAG.match(description or "")
    return int(m.group(1)) if m else None


def load(conn) -> dict:
    """Read every level the compare looks at.

    Args:
        conn: A connection from :func:`connect`.

    Returns:
        ``{categories, profiles, parts, labour, misc, projects, quotes, pos}``,
        each record carrying ``id`` (Velocity's) and ``legacy_id`` where the
        table has one.
    """
    data: dict = {}
    data["categories"] = _rows(conn, "SELECT id, type, name, legacy_id FROM categories")

    # Profiles with their contacts and phones nested
    profiles = _rows(conn, "SELECT id, type::text AS type, name, pst, address, postal_code, legacy_id FROM profiles")
    contacts = _rows(conn, "SELECT id, profile_id, name, job_title, email FROM contacts")
    phones = defaultdict(list)
    for p in _rows(conn, "SELECT contact_id, type::text AS type, number FROM contact_phones ORDER BY id"):
        phones[p["contact_id"]].append((p["type"], p["number"]))
    by_profile = defaultdict(list)
    for c in contacts:
        by_profile[c["profile_id"]].append({**c, "phones": phones[c["id"]]})
    for p in profiles:
        p["contacts"] = by_profile[p["id"]]
    data["profiles"] = profiles

    data["parts"] = _rows(conn, """
        SELECT p.id, p.part_number, p.description, p.cost, p.markup_percent, p.legacy_id,
               c.name AS category, v.name AS vendor
        FROM parts p LEFT JOIN categories c ON c.id = p.category_id LEFT JOIN profiles v ON v.id = p.vendor_id""")
    data["labour"] = _rows(conn, """
        SELECT l.id, l.description, l.hours, l.rate, l.markup_percent, l.legacy_id, c.name AS category
        FROM labor l LEFT JOIN categories c ON c.id = l.category_id""")
    data["misc"] = _rows(conn, """
        SELECT id, description, unit_price, markup_percent, is_system_item, legacy_id FROM miscellaneous""")
    data["projects"] = _rows(conn, """
        SELECT pr.id, pr.uca_project_number AS uca, pr.name, c.name AS customer, pr.created_on, pr.status,
               pr.ucsh_project_number AS ucsh, pr.project_lead AS lead, pr.legacy_id, pr.vision_project_number
        FROM projects pr LEFT JOIN profiles c ON c.id = pr.customer_id""")

    # Quotes: header, last change in Velocity, invoices raised, then lines
    quotes = _rows(conn, """
        SELECT q.id, pr.uca_project_number AS uca, q.quote_sequence, q.created_at, q.status AS stored_status,
               q.current_version - (SELECT count(*) FROM quote_snapshots s       -- versions the cutover tool made
                                    WHERE s.quote_id = q.id AND s.actor_user_id = :tool) AS current_version,
               q.item_list_version, q.client_po_number, q.work_description,
               q.legacy_imported, q.legacy_id, q.vision_wo_number, q.vision_closed,
               (SELECT max(s.created_at) FROM quote_snapshots s
                WHERE s.quote_id = q.id AND s.actor_user_id IS DISTINCT FROM :tool) AS last_change,
               (SELECT count(*) FROM invoices i WHERE i.quote_id = q.id) AS invoices
        FROM quotes q JOIN projects pr ON pr.id = q.project_id""", {"tool": TOOL_ACTOR})
    lines = _rows(conn, """
        SELECT li.id, li.quote_id, li.item_type,
               COALESCE(li.part_id, li.labor_id, li.misc_id) AS item_id,
               COALESCE(p.part_number, l.description, m.description) AS ref,
               li.description, li.quantity, li.unit_price, li.base_cost, li.qty_fulfilled, li.qty_pending, li.legacy_id
        FROM quote_line_items li
        LEFT JOIN parts p ON p.id = li.part_id
        LEFT JOIN labor l ON l.id = li.labor_id
        LEFT JOIN miscellaneous m ON m.id = li.misc_id""")
    by_quote = defaultdict(list)
    for li in lines:
        by_quote[li["quote_id"]].append(li)
    for q in quotes:
        q["lines"] = by_quote[q["id"]]
        q["wo"] = wo_of(q["work_description"])
        q["quote_number"] = f"{q['uca']}-{q['quote_sequence']:04d}-{q['item_list_version'] or 0}"   # as the app shows it
        q["status"] = compute_status_from_lines(                                                     # as the app shows it
            [SimpleNamespace(**l) for l in q["lines"]], q["client_po_number"], q["vision_closed"])
    data["quotes"] = quotes

    # Purchase orders: header, last change, receivings, then lines
    pos = _rows(conn, """
        SELECT po.id, pr.uca_project_number AS uca, po.po_sequence AS seq, v.name AS vendor, po.created_at,
               po.status::text AS status,
               po.current_version - (SELECT count(*) FROM po_snapshots s          -- versions the cutover tool made
                                     WHERE s.purchase_order_id = po.id AND s.actor_user_id = :tool
                                       AND s.action_type <> 'create') AS current_version,   -- "create" keeps version 0
               po.work_description, po.legacy_imported, po.legacy_id, po.vision_po_number,
               (SELECT max(s.created_at) FROM po_snapshots s
                WHERE s.purchase_order_id = po.id AND s.actor_user_id IS DISTINCT FROM :tool) AS last_change,
               (SELECT count(*) FROM po_receivings r WHERE r.purchase_order_id = po.id AND r.voided_at IS NULL) AS receivings
        FROM purchase_orders po JOIN projects pr ON pr.id = po.project_id LEFT JOIN profiles v ON v.id = po.vendor_id""", {"tool": TOOL_ACTOR})
    po_lines = _rows(conn, """
        SELECT li.id, li.purchase_order_id, li.item_type, li.part_id AS item_id, p.part_number, li.description, li.quantity, li.unit_price,
               li.qty_received, li.qty_pending, li.legacy_id
        FROM po_line_items li LEFT JOIN parts p ON p.id = li.part_id""")
    by_po = defaultdict(list)
    for li in po_lines:
        by_po[li["purchase_order_id"]].append(li)
    for po in pos:
        po["lines"] = by_po[po["id"]]
        po["status"] = po["status"].capitalize()           # enum name ("closed") -> label ("Closed")
        po["po_number"] = f"PO-{po['uca']}-{po['seq']:04d}-{po['current_version'] or 0}"
    data["pos"] = pos
    # First change Velocity recorded on any quote: the June import happened before it
    data["first_snapshot"] = conn.execute(text(
        "SELECT min(created_at) FROM quote_snapshots WHERE actor_user_id IS DISTINCT FROM :tool"), {"tool": TOOL_ACTOR}).scalar()
    # The June day a committed cutover run used: after a run its own snapshots and
    # added work orders would move the estimate, so later compares reuse it
    has_log = conn.execute(text("SELECT to_regclass('vision_sync_runs')")).scalar()
    recorded = conn.execute(text(
        "SELECT june_cutoff FROM vision_sync_runs WHERE mode = 'commit' ORDER BY id DESC LIMIT 1")).scalar()         if has_log else None
    data["recorded_cutoff"] = date.fromisoformat(recorded) if recorded else None
    return data
