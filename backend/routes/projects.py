from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, cast, func, literal, or_
from sqlalchemy.orm import Session, aliased, joinedload
from typing import Dict, List, Optional, Set
import re

import json
from datetime import datetime

import log_store
from auth import current_actor
from database import get_db
from models import ArchivedProjectRow, Invoice, Project, Profile, ProfileType, PurchaseOrder, Quote
from scripts.vision_delta.archive import (NotSafe, PREFIX, by_hand_file, by_hand_remove, index_entry, open_blockers,
                                          sha)
from schemas import (
    Paginated,
    ProjectCreate,
    ProjectUpdate,
    Project as ProjectSchema,
    ProjectFull,
    ProjectListView,
    ProjectSearchResult,
    MatchedQuoteRef,
    MatchedPORef,
    Quote as QuoteSchema,
)
from routes.purchase_orders import format_po_number

# See parts.py for the same constants — kept inline per-module for clarity.
DEFAULT_LIMIT = 50
MAX_LIMIT = 10000


def format_quote_number(uca_project_number: str, quote_sequence: int, current_version: int) -> str:
    """Format the full quote number: {UCA}-{Sequence:04d}-{Version}"""
    return f"{uca_project_number}-{quote_sequence:04d}-{current_version}"

router = APIRouter(prefix="/projects", tags=["projects"])


def increment_letter_prefix(prefix: str) -> str:
    """
    Increment letter prefix: A→B, Z→AA, AZ→BA, ZZ→AAA
    """
    if not prefix:
        return "A"

    chars = list(prefix)
    i = len(chars) - 1

    while i >= 0:
        if chars[i] < 'Z':
            chars[i] = chr(ord(chars[i]) + 1)
            return ''.join(chars)
        else:
            chars[i] = 'A'
            i -= 1

    # All chars were Z, need to add a new letter
    return 'A' + ''.join(chars)


def generate_next_uca_number(db: Session) -> str:
    """
    Generate the next UCA project number.
    Format: Letter(s) + 4-digit number (e.g., A0001, B0001, AA0001)
    - Starts at A0001
    - A0001 → A9999 → B0001 → ... → Z9999 → AA0001 → AB0001 → ...
    """
    existing = db.query(Project.uca_project_number).all()
    existing_numbers = [row[0] for row in existing if row[0]]
    # Archived projects keep their numbers (#319): archiving the newest project must not
    # free its number for the next new project, or two jobs (and their invoices) would share it
    existing_numbers += [row[0] for row in db.query(ArchivedProjectRow.uca)
                         .filter(ArchivedProjectRow.state == "archived").all() if row[0]]

    def parse_uca(uca: str) -> tuple:
        """Parse UCA into (letter_prefix, number)"""
        match = re.match(r'^([A-Z]+)(\d{4})$', uca)
        if match:
            return (match.group(1), int(match.group(2)))
        return ("", 0)

    def uca_sort_key(uca: str) -> tuple:
        """Sort key: (prefix_length, prefix, number)"""
        prefix, num = parse_uca(uca)
        return (len(prefix), prefix, num)

    # Filter to only valid-format numbers for auto-generation
    # (legacy imports may have plain integers like "1", "27", "3679")
    valid_numbers = [n for n in existing_numbers if re.match(r'^[A-Z]+\d{4}$', n)]
    if not valid_numbers:
        return "A0001"

    # Find the highest existing UCA number
    highest = max(valid_numbers, key=uca_sort_key)
    prefix, number = parse_uca(highest)

    # Increment
    if number < 9999:
        return f"{prefix}{number + 1:04d}"
    else:
        # Roll over to next letter prefix
        return f"{increment_letter_prefix(prefix)}0001"


@router.get("/", response_model=Paginated[ProjectSchema])
def get_all_projects(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    skip: int = Query(0, ge=0, deprecated=True, description="Deprecated alias for offset"),
    db: Session = Depends(get_db),
):
    """Paginated list of projects with customer info.

    Pass `limit=0` to fetch every row.
    """
    effective_offset = offset or skip
    base = db.query(Project).options(joinedload(Project.customer))
    total = base.with_entities(Project.id).count()
    q = base.order_by(Project.id).offset(effective_offset)
    if limit > 0:
        q = q.limit(limit)
    items = q.all()
    return Paginated[ProjectSchema](
        items=items, total=total, limit=limit, offset=effective_offset
    )


def _build_list_view_rows(db: Session, project_ids: Optional[Set[int]] = None) -> List[ProjectListView]:
    """
    Build the aggregated list-view rows for projects.

    Uses window functions to compute quote_count, po_count, and latest sequence/version
    per project in a single query — avoids hydrating line items or full child documents.
    invoice_count (invoices on the project's quotes) feeds the Projects page totals.
    Optionally filters to a specific set of project IDs (used by /search).
    """
    quote_agg = (
        db.query(
            Quote.project_id.label("pid"),
            Quote.quote_sequence.label("seq"),
            Quote.item_list_version.label("ver"),  # visible version for the quote number (#202)
            func.count(Quote.id).over(partition_by=Quote.project_id).label("cnt"),
            func.row_number()
            .over(partition_by=Quote.project_id, order_by=Quote.quote_sequence.desc())
            .label("rn"),
        )
        .subquery()
    )

    po_agg = (
        db.query(
            PurchaseOrder.project_id.label("pid"),
            PurchaseOrder.po_sequence.label("seq"),
            PurchaseOrder.current_version.label("ver"),
            func.count(PurchaseOrder.id).over(partition_by=PurchaseOrder.project_id).label("cnt"),
            func.row_number()
            .over(partition_by=PurchaseOrder.project_id, order_by=PurchaseOrder.po_sequence.desc())
            .label("rn"),
        )
        .subquery()
    )

    invoice_agg = (                                        # invoices per project, through their quotes
        db.query(Quote.project_id.label("pid"), func.count(Invoice.id).label("cnt"))
        .join(Invoice, Invoice.quote_id == Quote.id)
        .group_by(Quote.project_id)
        .subquery()
    )

    query = (
        db.query(
            Project.id,
            Project.name,
            Project.uca_project_number,
            Project.ucsh_project_number,
            Project.customer_id,
            Project.project_lead,
            Project.status,
            Project.created_on,
            Profile.name.label("customer_name"),
            func.coalesce(quote_agg.c.cnt, 0).label("quote_count"),
            quote_agg.c.seq.label("latest_quote_seq"),
            quote_agg.c.ver.label("latest_quote_ver"),
            func.coalesce(po_agg.c.cnt, 0).label("po_count"),
            po_agg.c.seq.label("latest_po_seq"),
            po_agg.c.ver.label("latest_po_ver"),
            func.coalesce(invoice_agg.c.cnt, 0).label("invoice_count"),   # 0 when it has none
        )
        .outerjoin(Profile, Project.customer_id == Profile.id)
        .outerjoin(quote_agg, and_(quote_agg.c.pid == Project.id, quote_agg.c.rn == 1))
        .outerjoin(po_agg, and_(po_agg.c.pid == Project.id, po_agg.c.rn == 1))
        .outerjoin(invoice_agg, invoice_agg.c.pid == Project.id)   # one row per project, so no fan-out
        .order_by(Project.id)
    )

    if project_ids is not None:
        if not project_ids:
            return []
        query = query.filter(Project.id.in_(project_ids))

    rows = query.all()

    result: List[ProjectListView] = []
    for r in rows:
        latest_quote_number = (
            format_quote_number(r.uca_project_number, r.latest_quote_seq, r.latest_quote_ver)
            if r.latest_quote_seq is not None
            else None
        )
        latest_po_number = (
            format_po_number(r.uca_project_number, r.latest_po_seq, r.latest_po_ver)
            if r.latest_po_seq is not None
            else None
        )
        result.append(
            ProjectListView(
                id=r.id,
                name=r.name,
                uca_project_number=r.uca_project_number,
                ucsh_project_number=r.ucsh_project_number,
                customer_id=r.customer_id,
                customer_name=r.customer_name or "",
                project_lead=r.project_lead,
                status=r.status,
                created_on=r.created_on,
                quote_count=r.quote_count,
                po_count=r.po_count,
                invoice_count=r.invoice_count,
                latest_quote_number=latest_quote_number,
                latest_po_number=latest_po_number,
            )
        )
    return result


@router.get("/list-view", response_model=List[ProjectListView])
def get_projects_list_view(db: Session = Depends(get_db)):
    """
    Lightweight aggregated list of projects for the Projects page.

    Returns a flat shape per project with child-doc counts and latest quote/PO
    numbers, computed in a single SQL query. Avoids hydrating line items, vendors,
    or cost codes — payload is tens of KB instead of MB for typical data sizes.
    """
    return _build_list_view_rows(db)


@router.get("/search", response_model=List[ProjectSearchResult])
def search_projects(q: str = "", db: Session = Depends(get_db)):
    """
    Server-side cross-entity search across projects, quotes, POs, and vendors.

    A project is returned if it matches directly (name, UCA, UCSH, customer,
    project lead, status) OR has matching child quotes or POs. Matched child
    refs are returned alongside so the UI can offer click-through hints.
    """
    term = q.strip()
    if not term:
        return []

    pattern = f"%{term}%"

    # Direct project matches (project fields + customer name)
    direct_q = (
        db.query(Project.id)
        .outerjoin(Profile, Project.customer_id == Profile.id)
        .filter(
            or_(
                Project.name.ilike(pattern),
                Project.uca_project_number.ilike(pattern),
                func.coalesce(Project.ucsh_project_number, "").ilike(pattern),
                func.coalesce(Profile.name, "").ilike(pattern),
                func.coalesce(Project.project_lead, "").ilike(pattern),
                Project.status.ilike(pattern),
            )
        )
    )
    direct_ids: Set[int] = {row[0] for row in direct_q.all()}

    # Quote matches — formatted quote_number is "{UCA}-{seq:04d}-{ver}"
    quote_number_expr = (
        Project.uca_project_number
        + literal("-")
        + func.lpad(cast(Quote.quote_sequence, String), 4, "0")
        + literal("-")
        + cast(Quote.item_list_version, String)  # match the visible version (#202)
    )
    quote_rows = (
        db.query(
            Quote.id,
            Quote.project_id,
            Quote.quote_sequence,
            Quote.item_list_version,  # visible version for the quote number (#202)
            Project.uca_project_number,
        )
        .join(Project, Quote.project_id == Project.id)
        .filter(
            or_(
                quote_number_expr.ilike(pattern),
                func.coalesce(Quote.client_po_number, "").ilike(pattern),
                func.coalesce(Quote.work_description, "").ilike(pattern),
            )
        )
        .order_by(Quote.project_id, Quote.quote_sequence)
        .all()
    )

    # PO matches — formatted po_number is "PO-{UCA}-{seq:04d}-{ver}"
    Vendor = aliased(Profile)
    po_number_expr = (
        literal("PO-")
        + Project.uca_project_number
        + literal("-")
        + func.lpad(cast(PurchaseOrder.po_sequence, String), 4, "0")
        + literal("-")
        + cast(PurchaseOrder.current_version, String)
    )
    po_rows = (
        db.query(
            PurchaseOrder.id,
            PurchaseOrder.project_id,
            PurchaseOrder.po_sequence,
            PurchaseOrder.current_version,
            Project.uca_project_number,
            Vendor.name.label("vendor_name"),
        )
        .join(Project, PurchaseOrder.project_id == Project.id)
        .outerjoin(Vendor, PurchaseOrder.vendor_id == Vendor.id)
        .filter(
            or_(
                po_number_expr.ilike(pattern),
                func.coalesce(Vendor.name, "").ilike(pattern),
                func.coalesce(PurchaseOrder.vendor_po_number, "").ilike(pattern),
                func.coalesce(PurchaseOrder.work_description, "").ilike(pattern),
            )
        )
        .order_by(PurchaseOrder.project_id, PurchaseOrder.po_sequence)
        .all()
    )

    matched_quotes_by_pid: Dict[int, List[MatchedQuoteRef]] = {}
    for r in quote_rows:
        matched_quotes_by_pid.setdefault(r.project_id, []).append(
            MatchedQuoteRef(
                id=r.id,
                quote_number=format_quote_number(r.uca_project_number, r.quote_sequence, r.item_list_version),
            )
        )

    matched_pos_by_pid: Dict[int, List[MatchedPORef]] = {}
    for r in po_rows:
        matched_pos_by_pid.setdefault(r.project_id, []).append(
            MatchedPORef(
                id=r.id,
                po_number=format_po_number(r.uca_project_number, r.po_sequence, r.current_version),
                vendor_name=r.vendor_name or "",
            )
        )

    all_project_ids = direct_ids | set(matched_quotes_by_pid.keys()) | set(matched_pos_by_pid.keys())
    if not all_project_ids:
        return []

    list_view_rows = _build_list_view_rows(db, project_ids=all_project_ids)

    result: List[ProjectSearchResult] = []
    for lv in list_view_rows:
        result.append(
            ProjectSearchResult(
                **lv.model_dump(),
                matched_quotes=matched_quotes_by_pid.get(lv.id, []),
                matched_pos=matched_pos_by_pid.get(lv.id, []),
            )
        )
    return result


@router.get("/{project_id}", response_model=ProjectFull)
def get_project(project_id: int, db: Session = Depends(get_db)):
    """Get a single project with its quote/PO summaries (no nested line items).

    Open a specific quote or PO via its own endpoint to load its line items.
    """
    project = (
        db.query(Project)
        .options(
            joinedload(Project.customer),
            joinedload(Project.quotes),
            joinedload(Project.purchase_orders).joinedload(PurchaseOrder.vendor),
        )
        .filter(Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Build response with computed quote_numbers and po_numbers
    response = ProjectFull.model_validate(project)
    for i, quote in enumerate(project.quotes):
        response.quotes[i].quote_number = format_quote_number(
            project.uca_project_number,
            quote.quote_sequence,
            quote.item_list_version  # visible version: line-item changes only (#202)
        )
    for i, po in enumerate(project.purchase_orders):
        response.purchase_orders[i].po_number = format_po_number(
            project.uca_project_number,
            po.po_sequence,
            po.current_version
        )
    return response


@router.post("/", response_model=ProjectSchema)
def create_project(project_data: ProjectCreate, db: Session = Depends(get_db)):
    """Create a new project with auto-generated UCA number."""
    # Verify customer exists and is of type CUSTOMER
    customer = db.query(Profile).filter(Profile.id == project_data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=400, detail="Customer not found")
    if customer.type != ProfileType.customer:
        raise HTTPException(status_code=400, detail="Profile must be of type 'customer'")

    # Generate next UCA number
    uca_number = generate_next_uca_number(db)

    db_project = Project(
        name=project_data.name,
        customer_id=project_data.customer_id,
        status=project_data.status,
        ucsh_project_number=project_data.ucsh_project_number,
        uca_project_number=uca_number,
        project_lead=project_data.project_lead,
    )
    db.add(db_project)
    db.commit()
    db.refresh(db_project)

    # Reload with customer relationship
    db_project = (
        db.query(Project)
        .options(joinedload(Project.customer))
        .filter(Project.id == db_project.id)
        .first()
    )
    return db_project


@router.put("/{project_id}", response_model=ProjectSchema)
def update_project(project_id: int, project_data: ProjectUpdate, db: Session = Depends(get_db)):
    """Update an existing project. UCA number cannot be changed."""
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project_data.name is not None:
        db_project.name = project_data.name
    if project_data.status is not None:
        db_project.status = project_data.status
    if project_data.ucsh_project_number is not None:
        db_project.ucsh_project_number = project_data.ucsh_project_number
    if project_data.project_lead is not None:
        db_project.project_lead = project_data.project_lead
    if project_data.customer_id is not None:
        # Verify new customer exists and is of type CUSTOMER
        customer = db.query(Profile).filter(Profile.id == project_data.customer_id).first()
        if not customer:
            raise HTTPException(status_code=400, detail="Customer not found")
        if customer.type != ProfileType.customer:
            raise HTTPException(status_code=400, detail="Profile must be of type 'customer'")
        db_project.customer_id = project_data.customer_id

    db.commit()
    db.refresh(db_project)

    # Reload with customer relationship
    db_project = (
        db.query(Project)
        .options(joinedload(Project.customer))
        .filter(Project.id == db_project.id)
        .first()
    )
    return db_project


@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """Delete a project and all its quotes/POs (cascade)."""
    db_project = db.query(Project).filter(Project.id == project_id).first()
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")

    db.delete(db_project)
    db.commit()
    return {"message": "Project deleted successfully"}


# ---- archive a project by hand (#319) ----------------------------------- #

def _archive_actor() -> str:
    """Who is archiving; refuses an anonymous request (same rule as the decisions page).

    Raises:
        HTTPException: 401 when no signed-in user is attached.
    """
    a = current_actor.get()
    if not a:
        raise HTTPException(status_code=401, detail="sign in to archive a project")
    return a.get("email") or a["user_id"]


def _archive_store():
    """The store the archive files go to.

    Raises:
        HTTPException: 503 when none is configured.
    """
    store = log_store.from_env()
    if store is None:
        raise HTTPException(status_code=503, detail="the archive store is not configured")
    return store


def _date_text(v):
    """A stored date value as plain text (the file already holds strings; guard anyway)."""
    return None if v is None else str(v)


@router.post("/{project_id}/archive")
def archive_project(project_id: int, db: Session = Depends(get_db)) -> dict:
    """Archive a live project by hand (signed-in). 409 with reasons when anything is open.

    Refuses when a quote or PO line still has quantity pending, or an unsettled
    cutover decision is on the project (see ``open_blockers``). Otherwise it builds
    the archive file server-side (format 2, invoices and their snapshots included),
    writes it to the store and reads it back to verify, inserts the
    ``archived_projects`` row, and deletes the project's rows -- the row insert and
    the row deletes commit together, so a failure anywhere leaves the project live.
    Any signed-in user may archive any closed project, of any year.

    Endpoint: ``POST /projects/{project_id}/archive``.

    Raises:
        HTTPException: 401 no actor, 404 no project, 409 open work / rows changed,
            502 the file did not read back the same, 503 no store.
    """
    who = _archive_actor()                                 # 401 when not signed in
    store = _archive_store()
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        raise HTTPException(status_code=404, detail="project not found")
    uca, vnum = proj.uca_project_number, proj.vision_project_number
    pending = (db.query(ArchivedProjectRow)                # already in the archive, live rows awaiting the bulk removal
               .filter(ArchivedProjectRow.velocity_project_id == project_id, ArchivedProjectRow.state == "archived",
                       ArchivedProjectRow.rows_removed.is_(False)).first())
    if pending is not None:
        raise HTTPException(status_code=409, detail={"reasons": ["already in the archive; its removal from the "
                                                                 "database is pending"]})
    blockers = open_blockers(db, project_id, uca, vnum)    # decision 3: refuse while anything is open
    if blockers:
        raise HTTPException(status_code=409, detail={"reasons": blockers})
    prior = (db.query(ArchivedProjectRow).filter(ArchivedProjectRow.uca == uca)   # carry a past Vision block over
             .order_by(ArchivedProjectRow.archived_at.desc().nullslast(), ArchivedProjectRow.id.desc()).first())
    prior_vision = None
    if prior is not None:
        try:
            prior_vision = (store.get_json(prior.file_key) or {}).get("vision")
        except KeyError:
            prior_vision = None
    f = by_hand_file(db, project_id, prior_vision)          # build the file from the live rows
    doc = json.loads(json.dumps(f, default=str))           # exactly what the file will hold (dates as strings)
    key = f"{PREFIX}/projects/{uca}.json"                   # default key; never overwrite a live archived row's file
    owned = (db.query(ArchivedProjectRow).filter(ArchivedProjectRow.file_key == key,
             ArchivedProjectRow.state == "archived").first())
    if owned is not None:                                  # a currently-archived row owns the default: pick a fresh key
        key = f"{PREFIX}/projects/{uca}-{int(datetime.utcnow().timestamp())}.json"
    store.put_json(key, doc)                               # write first; if this fails nothing is deleted
    if sha(store.get_json(key)) != sha(doc):               # read back: the store holds exactly this
        raise HTTPException(status_code=502, detail=f"{key} did not read back the same")
    entry = index_entry(doc, key)                          # the list's counts for the row
    db.add(ArchivedProjectRow(
        uca=uca, velocity_project_id=project_id, file_key=key, name=doc.get("name"), customer=doc.get("customer"),
        created_on=_date_text(doc.get("created_on")), last_activity=_date_text(doc.get("last_activity")),
        work_orders=entry["work_orders"], pos=entry["pos"], velocity_quotes=entry["velocity_quotes"],
        velocity_pos=entry["velocity_pos"], in_velocity=True, state="archived", archived_at=datetime.utcnow(),
        archived_by=who, rows_removed=True, source="by hand"))
    try:
        removed = by_hand_remove(db, doc)                  # delete the project's rows, children first
        db.commit()                                        # row insert + row deletes land together
    except NotSafe as e:
        db.rollback()                                      # project untouched: the file is an orphan, harmless
        raise HTTPException(status_code=409, detail={"reasons": [str(e)]}) from e
    return {"uca": uca, "archived": True, "file_key": key, "removed": removed}
