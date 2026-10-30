"""The decisions page: settle, one case at a time, what the Vision cutover left for a person.

Cases are loaded once per cutover run into ``vision_cases`` by
``scripts/vision_delta/cases.py``. The page walks the steps in
:data:`scripts.vision_delta.cases.KINDS` order and shows each case with what
was found, Velocity's value beside Vision's, and a predicted answer.

What a decision writes:
  * ``vision`` -- Vision's values are written to the Velocity record, each
    field only if Velocity still holds the value the cutover saw (otherwise
    the whole case is refused and nothing is written). A quote line's
    quantities are applied together and must add up. Quote changes write one
    "vision_sync" snapshot (the quote's visible number is unchanged); PO line
    changes write one too, as the cutover did.
  * ``velocity`` -- nothing changes in the record. On a quote or PO, Vision's
    version is added to its audit trail as a "vision_version" entry, so the
    other side stays on record (the visible number is unchanged).
  * ``same job`` / ``same order`` / ``copy replaces original`` -- a merge, like
    settling a merge conflict: the Velocity quote (PO) is kept and takes the
    other one's Vision WO # (PO #) and import key; the other one's lines are
    added to the kept record's audit trail as a "vision_version" entry, then
    the other one is deleted. Refused unless the deleted side is untouched in
    Velocity since it arrived (no person's edit, no invoice, no receiving).
  * ``duplicate`` -- the project holding the work is kept; the other one's
    quotes and POs move into it (each gets a "move" entry naming its old
    number), it takes the other's Vision number and import key, and the
    emptied project is deleted. A moved Vision quote (PO) that looks like one
    of the kept project's own opens a new same job (same order) case.
  * every other answer only records the decision: nothing needs to change.

Each decision stores who made it and when. A settled case cannot be settled
again from here. Writes need a signed-in user: the decision names them.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from auth import current_actor
from database import get_db
from models import (Invoice, InvoiceLineItem, Labor, Miscellaneous, Part, POLineItem, POLineItemSnapshot, POReceiving,
                    POReceivingLineItem, POSnapshot, POStatus, Profile, Project, PurchaseOrder, Quote, QuoteLineItem,
                    QuoteLineItemSnapshot, QuoteSnapshot, VisionCase)
from scripts.vision_delta.cases import KINDS, OPTIONS

router = APIRouter(prefix="/vision-decisions", tags=["vision decisions"])

# ---- how a logged field maps onto a Velocity record ---------------------- #
MODELS = {"quote_line_items": QuoteLineItem, "quotes": Quote, "purchase_orders": PurchaseOrder,
          "po_line_items": POLineItem, "parts": Part, "labor": Labor, "miscellaneous": Miscellaneous,
          "projects": Project, "profiles": Profile}
ATTRS = {                                                  # table -> logged field -> model attribute
    "quote_line_items": {f: f for f in ("description", "quantity", "unit_price", "base_cost", "qty_fulfilled", "qty_pending")},
    "quotes": {f: f for f in ("created_at", "work_description", "client_po_number")},
    "purchase_orders": {f: f for f in ("created_at", "work_description", "status")},
    "po_line_items": {f: f for f in ("description", "quantity", "unit_price", "qty_received", "qty_pending")},
    "parts": {f: f for f in ("part_number", "description", "cost", "markup_percent")},
    "labor": {f: f for f in ("description", "hours", "rate", "markup_percent")},
    "miscellaneous": {f: f for f in ("description", "unit_price", "markup_percent")},
    "projects": {"name": "name", "created_on": "created_on", "status": "status", "ucsh": "ucsh_project_number",
                 "lead": "project_lead"},
    "profiles": {f: f for f in ("pst", "address", "postal_code")},
}
NUMBERS = {"cost", "markup_percent", "hours", "rate", "unit_price", "base_cost", "quantity", "qty_fulfilled",
           "qty_pending", "qty_received"}
DATES = {"created_on", "created_at"}
QUOTE_QTY = ("quantity", "qty_fulfilled", "qty_pending")
PO_QTY = ("quantity", "qty_received", "qty_pending")
REFUSED_FIELDS = {"item": "a different inventory item: change it in the editor",
                  "vendor": "a different vendor: change it by hand",
                  "category": "a different category: change it by hand"}
STEP = {k: (label, about) for k, label, _, about in KINDS}


class Refused(Exception):
    """A decision that cannot be carried out; nothing was written."""


# ---- wire shapes --------------------------------------------------------- #

class DecideIn(BaseModel):
    """A person's answer to one case."""
    decision: str
    note: Optional[str] = None


class AcceptIn(BaseModel):
    """Accept the predicted answer on these open cases of one step."""
    case_ids: list[int]


# ---- small helpers ------------------------------------------------------- #

def _actor() -> str:
    """Who is deciding; refuses an anonymous request.

    Raises:
        HTTPException: 401 when no signed-in user is attached.
    """
    a = current_actor.get()
    if not a:
        raise HTTPException(status_code=401, detail="sign in to record a decision")
    return a.get("email") or a["user_id"]                  # email for people, id as a fallback


def _txt(v: Any) -> Optional[str]:
    """A value as the cutover log wrote it (dates in ISO form, enums by value)."""
    if v is None:
        return None
    if isinstance(v, POStatus):
        return v.value
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def same(now: Any, logged: Optional[str]) -> bool:
    """True when a record's current value is still the one the cutover logged.

    Numbers compare to 4 decimals, blank text equals None, line endings are ignored.
    """
    a, b = _txt(now), logged
    a = None if a is None or not a.strip() else a.replace("\r\n", "\n").strip()
    b = None if b is None or not b.strip() else b.replace("\r\n", "\n").strip()
    if a is None or b is None:
        return a == b
    try:
        return round(float(a), 4) == round(float(b), 4)    # 50 == 50.0
    except ValueError:
        return a == b


def _value(field: str, text: Optional[str]) -> Any:
    """Vision's logged text as the type the column holds."""
    if text is None or not str(text).strip():
        return None
    if field in NUMBERS:
        return float(text)
    if field in DATES:
        return datetime.fromisoformat(text)
    return text


def _relabel(row: dict) -> dict:
    """A logged row with a project status of "archived" read as "closed" (#330).

    The cutover logged project statuses before migration 037 renamed "archived" to
    "closed", so without this a status case would show the old word, compare
    unequal to the relabelled record, and write the retired value back.

    Args:
        row: One logged row of a case (``velocity`` / ``vision`` hold the logged text).

    Returns:
        The row, or a copy with either side's "archived" read as "closed".
    """
    if row.get("record_table") != "projects" or row.get("field") != "status":
        return row                                         # only the project status changed its word
    fix = lambda v: "closed" if v == "archived" else v     # other statuses pass through
    return dict(row, velocity=fix(row.get("velocity")), vision=fix(row.get("vision")))


def _record(db: Session, row: dict, lock: bool = False):
    """The Velocity record a logged row points at, or None."""
    model = MODELS.get(row.get("record_table"))
    if model is None or row.get("record_id") is None:
        return None
    q = db.query(model).filter(model.id == row["record_id"])
    return (q.with_for_update() if lock else q).first()


def _now(db: Session, row: dict) -> Any:
    """The record's current value of a logged field (for display), or None."""
    rec = _record(db, row)
    attr = ATTRS.get(row.get("record_table"), {}).get(row.get("field"))
    return getattr(rec, attr) if rec is not None and attr else None


def _link(db: Session, table: Optional[str], rid: Optional[int]) -> Optional[dict]:
    """Where a record opens in the app: ``{project_id, type, id}`` or None."""
    if rid is None:
        return None
    if table == "quote_line_items":
        li = db.get(QuoteLineItem, rid)
        return _link(db, "quotes", li.quote_id) if li else None
    if table == "quotes":
        q = db.get(Quote, rid)
        return {"project_id": q.project_id, "type": "quote", "id": q.id} if q else None
    if table == "po_line_items":
        li = db.get(POLineItem, rid)
        return _link(db, "purchase_orders", li.purchase_order_id) if li else None
    if table == "purchase_orders":
        po = db.get(PurchaseOrder, rid)
        return {"project_id": po.project_id, "type": "po", "id": po.id} if po else None
    if table == "projects":
        return {"project_id": rid, "type": "project", "id": rid}
    return None


def _po_by_number(db: Session, number: Optional[str]) -> Optional[PurchaseOrder]:
    """A Velocity PO from its printed number ``PO-<UCA>-<seq>-<version>``."""
    parts = (number or "").split("-")
    if len(parts) != 4 or parts[0] != "PO" or not parts[2].isdigit():
        return None
    return (db.query(PurchaseOrder).join(Project, PurchaseOrder.project_id == Project.id)
            .filter(Project.uca_project_number == parts[1], PurchaseOrder.po_sequence == int(parts[2])).first())


def case_out(db: Session, c: VisionCase) -> dict:
    """A case as the page shows it, with each field's current Velocity value and links."""
    detail = dict(c.detail or {})
    links = []
    rows = []
    for r in detail.get("rows", []):
        r = _relabel(r)                                    # a logged "archived" project status reads "closed"
        readable = r.get("field") in ATTRS.get(r.get("record_table"), {})   # item, vendor, category: not read back
        now = _now(db, r) if readable else None
        rows.append(dict(r, now=_txt(now) if readable else None,
                         changed_since=(not same(now, r["velocity"])) if readable else None))
        link = _link(db, r.get("record_table"), r.get("record_id"))
        if link and link not in links:
            links.append(link)
    found = detail.get("found")
    if c.kind == "same job" and found:                     # the Velocity quote, and the one added from Vision
        vq = found.get("velocity quote id")
        if vq and vq.isdigit():
            links.append(_link(db, "quotes", int(vq)))
        wo = found.get("vision WO")
        for q in (db.query(Quote).filter(Quote.vision_wo_number == int(wo)).all() if wo and wo.isdigit() else []):
            link = _link(db, "quotes", q.id)
            if link not in links:
                links.append(link)
    if c.kind == "same order" and found:
        for number in (found.get("velocity PO"),):
            po = _po_by_number(db, number)
            if po:
                links.append(_link(db, "purchase_orders", po.id))
        for po in db.query(PurchaseOrder).filter(PurchaseOrder.vision_po_number == found.get("vision PO")).all():
            link = _link(db, "purchase_orders", po.id)
            if link not in links:
                links.append(link)
    return {"id": c.id, "kind": c.kind, "project": c.project, "title": c.title, "prediction": c.prediction,
            "prediction_reason": c.prediction_reason, "options": OPTIONS.get(c.kind, []),
            "rows": rows, "found": found, "links": [l for l in links if l],
            "decision": c.decision, "decided_by": c.decided_by, "decided_at": c.decided_at,
            "decision_note": c.decision_note, "result": c.result}


# ---- carrying out a decision --------------------------------------------- #

def _quote_lines(db: Session, rows: list[dict]) -> dict[int, list[str]]:
    """Apply Vision's values to quote lines; returns quote id -> what changed.

    Raises:
        Refused: A line changed since the cutover, is on an invoice, or would not add up.
    """
    from scripts.vision_delta.apply import Writer
    from routes.quotes import round_quantity
    changed: dict[int, list[str]] = {}
    by_line: dict[int, list[dict]] = {}
    for r in rows:
        by_line.setdefault(r["record_id"], []).append(r)
    for line_id, lrows in by_line.items():
        li = db.query(QuoteLineItem).filter(QuoteLineItem.id == line_id).with_for_update().first()
        if li is None:
            raise Refused(f"quote line {line_id} no longer exists")
        if any(not same(getattr(li, r["field"]), r["velocity"]) for r in lrows):
            raise Refused(f"{lrows[0]['context']}: changed in Velocity since the cutover")
        touched = {r["field"] for r in lrows}
        if touched & set(QUOTE_QTY) and db.query(InvoiceLineItem).filter(
                InvoiceLineItem.quote_line_item_id == li.id).first():
            raise Refused(f"{lrows[0]['context']}: the line is on an invoice; change it in the quote editor")
        target = {f: getattr(li, f) for f in QUOTE_QTY}
        for r in lrows:
            target[r["field"]] = _value(r["field"], r["vision"])
        if "quantity" in touched and not touched & {"qty_fulfilled", "qty_pending"}:
            target["qty_pending"] = (target["quantity"] or 0) - (target["qty_fulfilled"] or 0)   # what is left to do
        elif touched & {"qty_fulfilled", "qty_pending"} and not touched >= {"qty_fulfilled", "qty_pending"}:
            other = "qty_pending" if "qty_fulfilled" in touched else "qty_fulfilled"
            target[other] = (target["quantity"] or 0) - (target["qty_fulfilled" if other == "qty_pending" else "qty_pending"] or 0)
        qty, done, left = (round_quantity(float(target[k] or 0)) for k in QUOTE_QTY)
        problem = Writer.line_problem(li.item_type, qty, done, left) if touched & set(QUOTE_QTY) else None   # text-only: no sum check
        if problem:
            raise Refused(f"{lrows[0]['context']}: {problem}")
        for r in lrows:
            if r["field"] not in QUOTE_QTY:
                setattr(li, r["field"], _value(r["field"], r["vision"]))   # description, unit price, base cost
        if touched & set(QUOTE_QTY):
            li.quantity, li.qty_fulfilled, li.qty_pending = qty, done, left
        changed.setdefault(li.quote_id, []).append(f"{lrows[0]['context']} ({', '.join(sorted(touched))})")
    return changed


def _po_lines(db: Session, rows: list[dict]) -> dict[int, list[str]]:
    """Apply Vision's values to PO lines; returns PO id -> what changed.

    Raises:
        Refused: A line changed since the cutover, was received against, or would not fit.
    """
    from scripts.vision_delta.apply import Writer
    changed: dict[int, list[str]] = {}
    by_line: dict[int, list[dict]] = {}
    for r in rows:
        by_line.setdefault(r["record_id"], []).append(r)
    for line_id, lrows in by_line.items():
        li = db.query(POLineItem).filter(POLineItem.id == line_id).with_for_update().first()
        if li is None:
            raise Refused(f"PO line {line_id} no longer exists")
        if any(not same(getattr(li, r["field"]), r["velocity"]) for r in lrows):
            raise Refused(f"{lrows[0]['context']}: changed in Velocity since the cutover")
        touched = {r["field"] for r in lrows}
        if touched & set(PO_QTY) and db.query(POReceivingLineItem).filter(
                POReceivingLineItem.po_line_item_id == li.id).first():
            raise Refused(f"{lrows[0]['context']}: the line has been received against; change it in the PO editor")
        target = {f: getattr(li, f) for f in PO_QTY}
        for r in lrows:
            target[r["field"]] = _value(r["field"], r["vision"])
        if "quantity" in touched and not touched & {"qty_received", "qty_pending"}:
            target["qty_pending"] = max(0, (target["quantity"] or 0) - (target["qty_received"] or 0))
        problem = Writer.po_line_problem(int(target["quantity"] or 0), int(target["qty_received"] or 0),
                                         int(target["qty_pending"] or 0))
        if problem:
            raise Refused(f"{lrows[0]['context']}: {problem}")
        for r in lrows:
            v = _value(r["field"], r["vision"])
            setattr(li, r["field"], int(v) if r["field"] in PO_QTY else v)   # PO quantities are whole numbers
        li.qty_pending = int(target["qty_pending"])
        changed.setdefault(li.purchase_order_id, []).append(f"{lrows[0]['context']} ({', '.join(sorted(touched))})")
    return changed


def apply_vision(db: Session, rows: list[dict]) -> dict:
    """Write Vision's logged values to Velocity, all or nothing.

    Args:
        db: The request's session (the caller commits).
        rows: The case's logged rows.

    Returns:
        What was written: ``{"records": n, "fields": n, "snapshots": [...]}``.

    Raises:
        Refused: Anything that cannot be applied exactly; the caller rolls back.
    """
    from routes.purchase_orders import create_po_snapshot
    from routes.quotes import compute_status_from_lines, create_snapshot
    if any(r.get("record_id") is None or not r.get("field") for r in rows):
        raise Refused("Vision's record is not in Velocity and is not stored here; enter it by hand")
    for r in rows:
        if r["field"] in REFUSED_FIELDS:
            raise Refused(f"{r['context']}: {REFUSED_FIELDS[r['field']]}")
        if r["field"] not in ATTRS.get(r["record_table"], {}):
            raise Refused(f"{r['context']}: field {r['field']} cannot be applied here")
    snapshots: list[str] = []
    quotes: dict[int, list[str]] = {}
    pos_lines: dict[int, list[str]] = {}

    # Quote lines and PO lines (their quantities move together)
    quotes.update(_quote_lines(db, [r for r in rows if r["record_table"] == "quote_line_items"]))
    pos_lines.update(_po_lines(db, [r for r in rows if r["record_table"] == "po_line_items"]))

    # Single-field records: quote and PO headers, inventory, projects, profiles
    for r in (_relabel(r) for r in rows if r["record_table"] not in ("quote_line_items", "po_line_items")):
        rec = _record(db, r, lock=True)
        attr = ATTRS[r["record_table"]][r["field"]]
        if rec is None:
            raise Refused(f"{r['context']}: the record no longer exists")
        if not same(getattr(rec, attr), r["velocity"]):
            raise Refused(f"{r['context']}: changed in Velocity since the cutover")
        value = _value(r["field"], r["vision"])
        if r["field"] in DATES and value is None:
            raise Refused(f"{r['context']}: Vision has no date; a created date is never blanked")
        if r["record_table"] == "purchase_orders" and r["field"] == "status":
            value = POStatus(value)
        setattr(rec, attr, value)
        if r["record_table"] == "quotes":
            quotes.setdefault(rec.id, []).append(f"{r['field']}")

    db.flush()
    for quote_id, what in quotes.items():                  # one snapshot per quote, status from the lines
        q = db.query(Quote).filter(Quote.id == quote_id).with_for_update().one()
        lines = db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == quote_id).all()
        q.status = compute_status_from_lines(lines, q.client_po_number, q.vision_closed)
        create_snapshot(db, q, "vision_sync", ("Vision value applied (decisions page): " + "; ".join(what))[:1000])
        snapshots.append(f"quote {quote_id}")
    for po_id, what in pos_lines.items():                  # PO line changes count as edits, as in the cutover
        po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).with_for_update().one()
        create_po_snapshot(db, po, "vision_sync", ("Vision value applied (decisions page): " + "; ".join(what))[:1000])
        snapshots.append(f"po {po_id}")
    return {"records": len({(r["record_table"], r["record_id"]) for r in rows}), "fields": len(rows),
            "snapshots": snapshots}


def record_vision_version(db: Session, rows: list[dict]) -> dict:
    """Keep Velocity's values and add Vision's version to the quote or PO audit trail.

    Writes one "vision_version" snapshot per quote or PO the rows touch; the
    visible document number does not change. Other records get nothing.

    Returns:
        ``{"snapshots": [...]}``.
    """
    from routes.purchase_orders import create_po_snapshot
    from routes.quotes import create_snapshot
    notes: dict[tuple[str, int], list[str]] = {}
    for r in rows:
        link = _link(db, r.get("record_table"), r.get("record_id"))
        if not link or link["type"] not in ("quote", "po"):
            continue
        what = f"{r['context']}" + (f" {r['field']}: Vision {r['vision']}, Velocity {r['velocity']}" if r.get("field") else
                                    ": Vision has it, Velocity does not")
        notes.setdefault((link["type"], link["id"]), []).append(what)
    out = []
    for (kind, rid), what in notes.items():
        text = ("Vision version kept for reference, Velocity's value stays: " + "; ".join(what))[:1000]
        if kind == "quote":
            create_snapshot(db, db.query(Quote).filter(Quote.id == rid).with_for_update().one(), "vision_version", text)
        else:
            create_po_snapshot(db, db.query(PurchaseOrder).filter(PurchaseOrder.id == rid).with_for_update().one(),
                               "vision_version", text)
        out.append(f"{kind} {rid}")
    return {"snapshots": out}


# ---- merges: one record kept, the other carried into its audit trail ------ #

# The answers that merge two records, by step.
MERGES = {"same job": "same job", "same order": "same order", "velocity copy": "copy replaces original",
          "duplicate project": "duplicate"}
# Snapshot types that leave a record's content as it arrived: the cutover's and this
# page's entries, and a move (a new project and number, same lines -- a duplicate
# project's merge writes one). Any other means a person edited the record.
QUOTE_UNTOUCHED = {"vision_sync", "vision_version", "move"}
PO_UNTOUCHED = {"create", "vision_sync", "vision_version", "move"}   # the cutover's PO insert writes "create"


def _quote_number(db: Session, q: Quote) -> str:
    """A quote's visible number, e.g. ``A3808-0002-1``."""
    from routes.quotes import format_quote_number
    return format_quote_number(db.get(Project, q.project_id).uca_project_number, q.quote_sequence,
                               q.item_list_version or 0)


def _po_number(db: Session, po: PurchaseOrder) -> str:
    """A PO's visible number, e.g. ``PO-A3808-0002-1``."""
    from routes.purchase_orders import format_po_number
    return format_po_number(db.get(Project, po.project_id).uca_project_number, po.po_sequence, po.current_version)


def _quote_by_number(db: Session, number: Optional[str]) -> Optional[Quote]:
    """A Velocity quote from its printed number ``<UCA>-<seq>-<version>``."""
    parts = (number or "").split("-")
    if len(parts) != 3 or not parts[1].isdigit():
        return None                                        # not a quote number
    return (db.query(Quote).join(Project, Quote.project_id == Project.id)
            .filter(Project.uca_project_number == parts[0], Quote.quote_sequence == int(parts[1]))
            .with_for_update().first())


def _quote_touched(db: Session, q: Quote) -> Optional[str]:
    """Why a quote may not be deleted by a merge, or None when it is untouched.

    Args:
        db: The request's session.
        q: The quote the merge would delete.

    Returns:
        The reason (an invoice, or a person's edit), or None.
    """
    if db.query(Invoice).filter(Invoice.quote_id == q.id).first():
        return "it has an invoice"                         # billed work is never deleted
    edited = db.query(QuoteSnapshot).filter(QuoteSnapshot.quote_id == q.id,
                                            QuoteSnapshot.action_type.notin_(QUOTE_UNTOUCHED)).first()
    return "it was edited in Velocity" if edited else None


def _po_touched(db: Session, po: PurchaseOrder) -> Optional[str]:
    """Why a PO may not be deleted by a merge, or None when it is untouched.

    Args:
        db: The request's session.
        po: The PO the merge would delete.

    Returns:
        The reason (a receiving, or a person's edit), or None.
    """
    if db.query(POReceiving).filter(POReceiving.purchase_order_id == po.id).first():
        return "it has been received against"              # received stock is never deleted
    edited = db.query(POSnapshot).filter(POSnapshot.purchase_order_id == po.id,
                                         POSnapshot.action_type.notin_(PO_UNTOUCHED)).first()
    return "it was edited in Velocity" if edited else None


def _take_key(db: Session, keep: Any, gone: Any, label: str) -> None:
    """Move the Vision import key from the deleted record to the kept one.

    The key is what a later cutover run pairs Vision's record with; carried
    over, a rerun finds the kept record instead of adding Vision's again.

    Raises:
        Refused: The kept record already carries a different Vision record's key.
    """
    if gone.legacy_id is None:
        return                                             # nothing to carry
    if keep.legacy_id is not None and (keep.legacy_source, keep.legacy_id) != (gone.legacy_source, gone.legacy_id):
        raise Refused(f"{label} already stands for another Vision record")
    source, key = gone.legacy_source, gone.legacy_id
    gone.legacy_source, gone.legacy_id = None, None        # free the unique key first
    db.flush()
    keep.legacy_source, keep.legacy_id = source, key


def _quote_vision_version(db: Session, keep: Quote, gone: Quote, text: str) -> None:
    """Add the deleted quote's lines to the kept quote's audit trail.

    Writes one "vision_version" entry holding the deleted quote's lines as
    they stood, then one holding the kept quote's own lines, so the newest
    entry still matches the quote. Neither changes its visible number.
    """
    from routes.quotes import create_snapshot
    actor = current_actor.get()
    keep.current_version = (keep.current_version or 0) + 1   # the audit counter, as create_snapshot does
    snap = QuoteSnapshot(quote_id=keep.id, version=keep.current_version, action_type="vision_version",
                         action_description=text[:1000], actor_user_id=actor["user_id"] if actor else None,
                         actor_email=actor.get("email") if actor else None)
    db.add(snap)
    db.flush()                                             # its id, for the lines
    for li in db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == gone.id).all():
        db.add(QuoteLineItemSnapshot(                       # the deleted quote's line, field for field
            snapshot_id=snap.id, original_line_item_id=li.id, item_type=li.item_type, labor_id=li.labor_id,
            part_id=li.part_id, misc_id=li.misc_id, description=li.description,
            description_override=li.description_override, quantity=li.quantity, unit_price=li.unit_price,
            qty_pending=li.qty_pending, qty_fulfilled=li.qty_fulfilled, is_deleted=False, is_pms=li.is_pms,
            pms_percent=li.pms_percent, original_markup_percent=li.original_markup_percent,
            base_cost=li.base_cost, markup_percent=li.markup_percent))
    create_snapshot(db, keep, "vision_version", "Merge done: this quote's own lines stay")   # newest = the quote now


def _po_vision_version(db: Session, keep: PurchaseOrder, gone: PurchaseOrder, text: str) -> None:
    """Add the deleted PO's lines to the kept PO's audit trail (see :func:`_quote_vision_version`)."""
    from routes.purchase_orders import create_po_snapshot
    actor = current_actor.get()
    snap = POSnapshot(purchase_order_id=keep.id, version=keep.current_version, action_type="vision_version",
                      action_description=text[:1000], actor_user_id=actor["user_id"] if actor else None,
                      actor_email=actor.get("email") if actor else None)   # a vision version keeps the PO's version
    db.add(snap)
    db.flush()
    for li in db.query(POLineItem).filter(POLineItem.purchase_order_id == gone.id).all():
        db.add(POLineItemSnapshot(                          # the deleted PO's line, field for field
            snapshot_id=snap.id, original_line_item_id=li.id, item_type=li.item_type, part_id=li.part_id,
            description=li.description, quantity=li.quantity, unit_price=li.unit_price,
            qty_pending=li.qty_pending, qty_received=li.qty_received,
            actual_unit_price=li.actual_unit_price, is_deleted=False))
    create_po_snapshot(db, keep, "vision_version", "Merge done: this PO's own lines stay")


def merge_quotes(db: Session, keep: Optional[Quote], gone: Optional[Quote], why: str) -> dict:
    """Merge two quotes of one job: keep one, carry the other into its audit trail, delete it.

    Args:
        db: The request's session (the caller commits).
        keep: The Velocity quote that stays.
        gone: The quote the merge deletes (added from Vision, or the June original).
        why: The step, for the audit text.

    Returns:
        ``{"merged": "quotes", "kept": number, "deleted": number, "vision_wo_number": n}``.

    Raises:
        Refused: Either quote is missing, they are one quote, the deleted one
            was touched, or the Vision numbers disagree.
    """
    if keep is None or gone is None:
        raise Refused("one of the two quotes no longer exists")
    if keep.id == gone.id:
        raise Refused("both sides are the same quote")
    keep_no, gone_no = _quote_number(db, keep), _quote_number(db, gone)
    touched = _quote_touched(db, gone)
    if touched:
        raise Refused(f"{gone_no} cannot be deleted: {touched}; merge it by hand")
    wo = gone.vision_wo_number or gone.legacy_id           # the Vision work order the deleted quote stands for
    if keep.vision_wo_number and wo and keep.vision_wo_number != wo:
        raise Refused(f"{keep_no} already carries Vision WO {keep.vision_wo_number}")
    _take_key(db, keep, gone, keep_no)
    keep.vision_wo_number = keep.vision_wo_number or wo
    keep.vision_closed = keep.vision_closed or gone.vision_closed   # Vision's finished flag follows the job
    _quote_vision_version(db, keep, gone,
                          f"Merged with {gone_no} ({why}, Vision WO {wo}): its lines as they were, kept for "
                          f"reference; {gone_no} was deleted")
    db.delete(gone)                                        # its lines and audit entries go with it
    db.flush()
    return {"merged": "quotes", "kept": keep_no, "deleted": gone_no, "vision_wo_number": wo}


def merge_pos(db: Session, keep: Optional[PurchaseOrder], gone: Optional[PurchaseOrder]) -> dict:
    """Merge two POs of one order: keep the Velocity one, carry the Vision one into its audit trail, delete it.

    Returns:
        ``{"merged": "pos", "kept": number, "deleted": number, "vision_po_number": n}``.

    Raises:
        Refused: As :func:`merge_quotes`.
    """
    if keep is None or gone is None:
        raise Refused("one of the two POs no longer exists")
    if keep.id == gone.id:
        raise Refused("both sides are the same PO")
    keep_no, gone_no = _po_number(db, keep), _po_number(db, gone)
    touched = _po_touched(db, gone)
    if touched:
        raise Refused(f"{gone_no} cannot be deleted: {touched}; merge it by hand")
    number = gone.vision_po_number
    if keep.vision_po_number and number and keep.vision_po_number != number:
        raise Refused(f"{keep_no} already carries Vision PO {keep.vision_po_number}")
    _take_key(db, keep, gone, keep_no)
    keep.vision_po_number = keep.vision_po_number or number
    _po_vision_version(db, keep, gone, f"Merged with {gone_no} (same order, Vision PO {number}): its lines as "
                                       f"they were, kept for reference; {gone_no} was deleted")
    db.delete(gone)                                        # its lines and audit entries go with it
    db.flush()
    return {"merged": "pos", "kept": keep_no, "deleted": gone_no, "vision_po_number": number}


def _count(v: Any) -> int:
    """A count from the report's text, 0 when blank."""
    return int(float(v)) if v not in (None, "") else 0


def merge_projects(db: Session, found: Any, run_id: Optional[int] = None) -> dict:
    """Merge a project entered twice: move the other's quotes and POs into the one holding the work.

    The kept project is the one that held the most quotes and POs before the
    cutover; a tie is refused. Each moved quote and PO takes the next number
    in the kept project and gets a "move" audit entry with its old number,
    as the app's Move does. The emptied project is deleted.

    Args:
        db: The request's session (the caller commits).
        found: The case's report rows, one per project of the group.
        run_id: The case's cutover run; when given, a moved Vision record
            beside a look-alike of the kept project's own gets a new same job
            (same order) case in that run, so a person still decides the pair.

    Returns:
        ``{"merged": "projects", "kept": uca, "deleted": uca, "moved": [old -> new, ...],
        "new_cases": [title, ...]}``.

    Raises:
        Refused: Not exactly two projects, no clear one to keep, one is gone,
            or the kept one already stands for another Vision project.
    """
    from routes.purchase_orders import create_po_snapshot, get_next_po_sequence
    from routes.quotes import create_snapshot, get_next_quote_sequence
    rows = found if isinstance(found, list) else []
    if len(rows) != 2:
        raise Refused("only a pair of projects can be merged here; merge larger groups by hand")
    rank = lambda r: (_count(r.get("quotes")) + _count(r.get("pos")), -datetime.fromisoformat(r["created"]).timestamp()
                      if r.get("created") else 0)        # most records, then the older
    a, b = sorted(rows, key=rank, reverse=True)
    if _count(a.get("quotes")) + _count(a.get("pos")) == _count(b.get("quotes")) + _count(b.get("pos")):
        raise Refused("both projects held as much before the cutover; move the records by hand")
    keep = db.query(Project).filter(Project.uca_project_number == a["uca"]).with_for_update().first()
    gone = db.query(Project).filter(Project.uca_project_number == b["uca"]).with_for_update().first()
    if keep is None or gone is None:
        raise Refused("one of the two projects no longer exists")
    vision_no = gone.vision_project_number or (gone.uca_project_number if gone.legacy_id is not None else None)
    if keep.vision_project_number and vision_no and keep.vision_project_number != vision_no:
        raise Refused(f"{keep.uca_project_number} already carries Vision project {keep.vision_project_number}")
    _take_key(db, keep, gone, keep.uca_project_number)
    keep.vision_project_number = keep.vision_project_number or vision_no
    # The kept project's own Velocity records, before anything moves in: a moved
    # Vision record may be the same job as one of them, which the cutover never
    # compared because the two sat in different projects.
    own_quotes = db.query(Quote).filter(Quote.project_id == keep.id, Quote.legacy_id.is_(None)).all()
    own_pos = db.query(PurchaseOrder).filter(PurchaseOrder.project_id == keep.id,
                                             PurchaseOrder.legacy_id.is_(None)).all()
    moved_quotes, moved_pos = [], []
    moved = []
    note = f"when duplicate project {gone.uca_project_number} was merged into {keep.uca_project_number}"
    for q in db.query(Quote).filter(Quote.project_id == gone.id).order_by(Quote.quote_sequence).with_for_update():
        old = _quote_number(db, q)
        q.project_id, q.quote_sequence = keep.id, get_next_quote_sequence(db, keep.id)   # the app's Move
        db.flush()
        new = _quote_number(db, q)
        create_snapshot(db, q, "move", f"Moved from {gone.uca_project_number} ({old}) to "
                                       f"{keep.uca_project_number} {note}")
        moved.append(f"{old} -> {new}")
        moved_quotes.append(q)
    for po in db.query(PurchaseOrder).filter(PurchaseOrder.project_id == gone.id).order_by(
            PurchaseOrder.po_sequence).with_for_update():
        old = _po_number(db, po)
        po.project_id, po.po_sequence = keep.id, get_next_po_sequence(db, keep.id)
        db.flush()
        new = _po_number(db, po)
        create_po_snapshot(db, po, "move", f"Moved from {gone.uca_project_number} ({old}) to "
                                           f"{keep.uca_project_number} {note}")
        moved.append(f"{old} -> {new}")
        moved_pos.append(po)
    db.flush()
    left = (db.query(Quote).filter(Quote.project_id == gone.id).count()
            + db.query(PurchaseOrder).filter(PurchaseOrder.project_id == gone.id).count())
    if left:                                               # never delete a project that still holds work
        raise Refused(f"{gone.uca_project_number} still holds {left} records")
    gone_uca = gone.uca_project_number
    db.expunge(gone)                                       # its stale quote/PO lists must not cascade
    db.query(Project).filter(Project.id == gone.id).delete(synchronize_session=False)
    db.flush()
    new_cases = _pair_cases(db, run_id, keep, own_quotes, moved_quotes, own_pos, moved_pos) if run_id else []
    return {"merged": "projects", "kept": keep.uca_project_number, "deleted": gone_uca, "moved": moved,
            "new_cases": new_cases}


def _item_keys(lines: list) -> set:
    """What a quote's or PO's lines order: inventory item ids, else the description."""
    keys = set()
    for li in lines:
        ref = tuple(getattr(li, f, None) for f in ("part_id", "labor_id", "misc_id"))
        keys.add((li.item_type, ref) if any(ref) else (li.item_type, (li.description or "").strip().lower()))
    return keys


def _pair_cases(db: Session, run_id: int, keep: Project, own_quotes: list, moved_quotes: list,
                own_pos: list, moved_pos: list) -> list[str]:
    """Open a same job (same order) case for each moved Vision record beside a look-alike Velocity one.

    A duplicate project's merge brings its Vision quotes and POs beside the kept
    project's own; the cutover only compared records within one project, so
    these pairs were never put to a person. Quotes pair when they share an
    ordered item, POs when they go to the same vendor -- the cutover's tests.
    Each new case lands in its step, where its answer merges as usual.

    Args:
        db: The request's session (the caller commits).
        run_id: The cutover run the duplicate-project case belongs to.
        keep: The kept project.
        own_quotes, own_pos: Its own Velocity records, from before the move.
        moved_quotes, moved_pos: What moved in.

    Returns:
        The new cases' titles.
    """
    made: list[str] = []

    def add(kind: str, key: str, title: str, found: dict, reason: str) -> None:
        """Insert one case unless the run already has it."""
        if db.query(VisionCase).filter(VisionCase.run_id == run_id, VisionCase.kind == kind,
                                       VisionCase.case_key == key).first():
            return                                         # already open or settled
        db.add(VisionCase(run_id=run_id, kind=kind, case_key=key, project=keep.uca_project_number, title=title,
                          prediction=None, prediction_reason=reason, detail={"found": found}, change_ids=[]))
        made.append(title)

    for vq in (q for q in moved_quotes if q.legacy_id is not None and q.vision_wo_number):   # Vision work orders
        v_items = _item_keys(db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == vq.id).all())
        for oq in own_quotes:
            o_lines = db.query(QuoteLineItem).filter(QuoteLineItem.quote_id == oq.id).all()
            shared = len(v_items & _item_keys(o_lines))
            if not shared:
                continue                                   # nothing in common: a separate job
            number = _quote_number(db, oq)
            add("same job", f"wo:{vq.vision_wo_number}:{oq.id}",
                f"{keep.uca_project_number}: Vision WO {vq.vision_wo_number} beside {number}",
                {"project": keep.uca_project_number, "vision WO": str(vq.vision_wo_number),
                 "vision quote": _quote_number(db, vq), "velocity quote": number, "velocity quote id": str(oq.id),
                 "items in both": str(shared), "vision lines": str(len(v_items)), "velocity lines": str(len(o_lines))},
                f"moved in with duplicate project; {shared} item(s) in both")
    for vp in (p for p in moved_pos if p.legacy_id is not None and p.vision_po_number):     # Vision POs
        for op in own_pos:
            if op.vendor_id != vp.vendor_id:
                continue                                   # another vendor: another order
            number = _po_number(db, op)
            add("same order", f"po:{vp.vision_po_number}:{number}",
                f"{keep.uca_project_number}: Vision PO {vp.vision_po_number} beside {number}",
                {"project": keep.uca_project_number, "vision PO": vp.vision_po_number,
                 "velocity PO": number, "evidence": "same vendor"},
                "moved in with duplicate project; same vendor")
    db.flush()
    return made


def merge(db: Session, c: VisionCase) -> dict:
    """Carry out the merge a case's answer names.

    Raises:
        Refused: The case's records cannot be found or merged.
    """
    from scripts.vision_delta.apply import SOURCE
    found = (c.detail or {}).get("found") or {}
    if c.kind == "duplicate project":
        return merge_projects(db, found, c.run_id)         # opens cases for look-alikes it brings together
    if c.kind == "same job":
        vq, wo = found.get("velocity quote id"), found.get("vision WO")
        keep = db.query(Quote).filter(Quote.id == int(vq)).with_for_update().first() if (vq or "").isdigit() else None
        gone = (db.query(Quote).filter(Quote.legacy_source == SOURCE["quotes"], Quote.legacy_id == int(wo))
                .with_for_update().first() if (wo or "").isdigit() else None)   # the quote the cutover added
        return merge_quotes(db, keep, gone, "same job")
    if c.kind == "velocity copy":                          # the copy people worked on stays
        return merge_quotes(db, _quote_by_number(db, found.get("copy")), _quote_by_number(db, found.get("original")),
                            "copy replaces original")
    keep = _po_by_number(db, found.get("velocity PO"))
    if keep is not None:
        keep = db.query(PurchaseOrder).filter(PurchaseOrder.id == keep.id).with_for_update().one()   # lock it
    gone = (db.query(PurchaseOrder).filter(PurchaseOrder.legacy_source == SOURCE["pos"],
                                           PurchaseOrder.vision_po_number == found.get("vision PO"))
            .with_for_update().first())                    # the PO the cutover added
    return merge_pos(db, keep, gone)


def settle(db: Session, c: VisionCase, decision: str, who: str, note: Optional[str] = None) -> VisionCase:
    """Carry out one decision and record it (the caller commits).

    Raises:
        Refused: The decision is not allowed, or cannot be carried out.
    """
    if c.decision:
        raise Refused(f"already settled: {c.decision} by {c.decided_by}")
    if decision not in OPTIONS.get(c.kind, []):
        raise Refused(f"'{decision}' is not an answer for {c.kind}")
    rows = (c.detail or {}).get("rows", [])
    result: Optional[dict] = None
    if decision == "vision":
        result = apply_vision(db, rows)
    elif decision == "velocity":
        result = record_vision_version(db, rows)
    elif decision == MERGES.get(c.kind):
        result = merge(db, c)                              # one record kept, the other carried and deleted
    c.decision, c.decided_by, c.decided_at = decision, who, datetime.utcnow()
    c.decision_note, c.result = (note or None), result
    db.flush()
    return c


# ---- routes -------------------------------------------------------------- #

@router.get("/steps")
def steps(db: Session = Depends(get_db)) -> list[dict]:
    """Every step of the page in order, with its open and settled counts."""
    from sqlalchemy import func
    counts = {(k, d): n for k, d, n in db.query(VisionCase.kind, VisionCase.decision.isnot(None), func.count())
              .group_by(VisionCase.kind, VisionCase.decision.isnot(None))}
    predicted = dict(db.query(VisionCase.kind, func.count()).filter(
        VisionCase.decision.is_(None), VisionCase.prediction.isnot(None)).group_by(VisionCase.kind))
    unmerged = {k: len(_unmerged(db, k)) for k in MERGES}  # merge answers recorded before merges existed
    return [{"kind": k, "label": label, "about": about, "options": OPTIONS[k],
             "open": counts.get((k, False), 0), "settled": counts.get((k, True), 0),
             "open_with_prediction": predicted.get(k, 0), "unmerged": unmerged.get(k, 0)}
            for k, label, _, about in KINDS]


def _unmerged(db: Session, kind: str) -> list[VisionCase]:
    """Settled cases of a step whose merge answer was only recorded, not carried out.

    Args:
        db: The request's session.
        kind: A step with a merge answer.

    Returns:
        The cases answered with the step's merge answer whose result holds no merge.
    """
    settled = db.query(VisionCase).filter(VisionCase.kind == kind, VisionCase.decision == MERGES[kind])
    return [c for c in settled.order_by(VisionCase.id) if not (c.result or {}).get("merged")]


@router.post("/steps/{kind}/carry-out")
def carry_out(kind: str, db: Session = Depends(get_db)) -> dict:
    """Carry out the merge on cases settled with a merge answer before merges existed.

    Each case on its own: one that cannot be merged is reported and left as it
    was; the others are kept. The original decision, its person and time stay;
    the result records who carried it out.
    """
    who = _actor()
    if kind not in MERGES:
        raise HTTPException(status_code=404, detail=f"no merge answer in step {kind}")
    done, refused = [], []
    for c in _unmerged(db, kind)[:200]:                    # one request stays short
        sp = db.begin_nested()                             # each case all or nothing
        try:
            result = merge(db, c)
            c.result = dict(result, recorded_earlier=c.result, carried_out_by=who,
                            carried_out_at=datetime.utcnow().isoformat(sep=" "))
            db.flush()
            sp.commit()
            done.append(c.id)
        except (Refused, ValueError) as e:
            sp.rollback()
            refused.append({"id": c.id, "title": c.title, "reason": str(e)})
    db.commit()
    return {"merged": done, "refused": refused}


@router.get("/cases")
def cases(kind: str, status: str = Query("open", pattern="^(open|settled|all)$"),
          offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
          db: Session = Depends(get_db)) -> dict:
    """One page of a step's cases, open ones first by project."""
    if kind not in OPTIONS:
        raise HTTPException(status_code=404, detail=f"no step {kind}")
    q = db.query(VisionCase).filter(VisionCase.kind == kind)
    if status == "open":
        q = q.filter(VisionCase.decision.is_(None))
    elif status == "settled":
        q = q.filter(VisionCase.decision.isnot(None))
    total = q.count()
    page = q.order_by(VisionCase.project, VisionCase.id).offset(offset).limit(limit).all()
    return {"total": total, "cases": [case_out(db, c) for c in page]}


@router.post("/cases/{case_id}/decide")
def decide(case_id: int, body: DecideIn, db: Session = Depends(get_db)) -> dict:
    """Settle one case. 409 with the reason when it cannot be carried out (nothing written)."""
    who = _actor()
    c = db.query(VisionCase).filter(VisionCase.id == case_id).with_for_update().first()
    if c is None:
        raise HTTPException(status_code=404, detail="case not found")
    try:
        settle(db, c, body.decision, who, body.note)
    except (Refused, ValueError) as e:                     # ValueError: a logged value that does not fit the column
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e))
    db.commit()
    return case_out(db, c)


@router.post("/steps/{kind}/accept")
def accept(kind: str, body: AcceptIn, db: Session = Depends(get_db)) -> dict:
    """Accept the predicted answer on the given open cases of a step, each on its own.

    A case that cannot be carried out is left open and reported; the others are kept.
    """
    who = _actor()
    if kind not in OPTIONS:
        raise HTTPException(status_code=404, detail=f"no step {kind}")
    done, refused = [], []
    for cid in body.case_ids[:200]:                        # one request stays short
        c = db.query(VisionCase).filter(VisionCase.id == cid, VisionCase.kind == kind).with_for_update().first()
        if c is None or c.decision or not c.prediction:
            continue
        sp = db.begin_nested()                             # each case all or nothing
        try:
            settle(db, c, c.prediction, who, "accepted the predicted answer")
            sp.commit()
            done.append(cid)
        except (Refused, ValueError) as e:
            sp.rollback()
            refused.append({"id": cid, "title": c.title, "reason": str(e)})
    db.commit()
    return {"settled": done, "refused": refused}
