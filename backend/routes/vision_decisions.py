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
  * ``same job`` / ``same order`` -- the Vision WO # (PO #) is recorded on the
    Velocity quote (PO) when it has none, so the two show as one job.
  * every other answer only records the decision.

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
from models import (InvoiceLineItem, Labor, Miscellaneous, Part, POLineItem, POReceivingLineItem, POStatus, Profile,
                    Project, PurchaseOrder, Quote, QuoteLineItem, VisionCase)
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
    for r in (r for r in rows if r["record_table"] not in ("quote_line_items", "po_line_items")):
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


def link_twin(db: Session, c: VisionCase) -> dict:
    """Record the Vision WO # (PO #) on the Velocity quote (PO) of a confirmed same job.

    Raises:
        Refused: The Velocity record is gone or already carries another Vision number.
    """
    found = c.detail.get("found") or {}
    if c.kind == "same job":
        vq = found.get("velocity quote id")
        q = db.query(Quote).filter(Quote.id == int(vq)).with_for_update().first() if vq and vq.isdigit() else None
        wo = int(found["vision WO"]) if (found.get("vision WO") or "").isdigit() else None   # the column is a number
        if q is None or wo is None:
            raise Refused("the Velocity quote or the Vision WO # is missing")
        if q.vision_wo_number and q.vision_wo_number != wo:
            raise Refused(f"the Velocity quote already carries Vision WO {q.vision_wo_number}")
        q.vision_wo_number = wo
        return {"quote": q.id, "vision_wo_number": wo}
    po = _po_by_number(db, found.get("velocity PO"))
    number = found.get("vision PO")
    if po is None:
        raise Refused("the Velocity PO was not found")
    if po.vision_po_number and po.vision_po_number != number:
        raise Refused(f"the Velocity PO already carries Vision PO {po.vision_po_number}")
    po.vision_po_number = number
    return {"po": po.id, "vision_po_number": number}


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
    elif decision in ("same job", "same order"):
        result = link_twin(db, c)
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
    return [{"kind": k, "label": label, "about": about, "options": OPTIONS[k],
             "open": counts.get((k, False), 0), "settled": counts.get((k, True), 0),
             "open_with_prediction": predicted.get(k, 0)}
            for k, label, _, about in KINDS]


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
