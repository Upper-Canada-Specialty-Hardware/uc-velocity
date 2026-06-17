from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from datetime import datetime, date

from database import get_db
from auth import current_actor
from utils import to_naive_utc
from models import (
    Quote, QuoteLineItem, Invoice, InvoiceLineItem,
    QuoteSnapshot, QuoteLineItemSnapshot, Labor, Part, Miscellaneous,
    Project, Profile, CompanySettings,
    InvoiceSnapshot, InvoiceLineItemSnapshot
)
from schemas import (
    Invoice as InvoiceSchema,
    InvoiceCreate,
    InvoiceStatusUpdate,
    LineItemFulfillment,
    InvoiceSummaryItem,
    InvoiceSnapshot as InvoiceSnapshotSchema,
    InvoiceRevertPreview,
    CreatedAtUpdate
)
from routes.quotes import populate_invoice_number

router = APIRouter(prefix="/invoices", tags=["invoices"])


def create_invoice_snapshot(
    db: Session,
    invoice: Invoice,
    action_type: str,
    action_description: str,
) -> InvoiceSnapshot:
    """
    Create a snapshot of the current invoice state (mirrors create_snapshot for quotes).

    Increments invoice.current_version by 1, writes an InvoiceSnapshot (capturing the
    invoice's created_at in entity_created_at so a revert can restore the date) plus a
    snapshot of every invoice line item, and stamps the acting user from the request
    context.
    """
    new_version = invoice.current_version + 1
    invoice.current_version = new_version

    actor = current_actor.get()
    snapshot = InvoiceSnapshot(
        invoice_id=invoice.id,
        version=new_version,
        action_type=action_type,
        action_description=action_description,
        entity_created_at=invoice.created_at,
        actor_user_id=actor["user_id"] if actor else None,
        actor_email=actor.get("email") if actor else None,
    )
    db.add(snapshot)
    db.flush()  # Get the snapshot ID

    line_items = (
        db.query(InvoiceLineItem)
        .filter(InvoiceLineItem.invoice_id == invoice.id)
        .all()
    )
    for item in line_items:
        db.add(InvoiceLineItemSnapshot(
            snapshot_id=snapshot.id,
            original_line_item_id=item.id,
            quote_line_item_id=item.quote_line_item_id,
            item_type=item.item_type,
            description=item.description,
            unit_price=item.unit_price,
            qty_ordered=item.qty_ordered,
            qty_fulfilled_this_invoice=item.qty_fulfilled_this_invoice,
            qty_fulfilled_total=item.qty_fulfilled_total,
            qty_pending_after=item.qty_pending_after,
            labor_id=item.labor_id,
            part_id=item.part_id,
            misc_id=item.misc_id,
        ))

    return snapshot


@router.get("/", response_model=List[InvoiceSummaryItem])
def list_invoices(
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
    project_id: Optional[int] = Query(None, description="Optional: filter to a single project"),
    db: Session = Depends(get_db)
):
    """List invoices within a date range with project/customer info for the summary report."""
    # Fetch HST rate from company settings
    settings = db.query(CompanySettings).first()
    hst_rate = settings.hst_rate if settings and settings.hst_rate is not None else 13.0

    # Query invoices with joined quote -> project -> customer
    query = (
        db.query(Invoice)
        .join(Quote, Invoice.quote_id == Quote.id)
        .join(Project, Quote.project_id == Project.id)
        .join(Profile, Project.customer_id == Profile.id)
        .options(
            joinedload(Invoice.line_items),
            joinedload(Invoice.quote).joinedload(Quote.project).joinedload(Project.customer),
        )
        .filter(
            Invoice.created_at >= datetime.combine(start_date, datetime.min.time()),
            Invoice.created_at <= datetime.combine(end_date, datetime.max.time()),
            Invoice.status != "Voided",
        )
    )
    if project_id is not None:
        query = query.filter(Project.id == project_id)
    invoices = query.order_by(Invoice.created_at, Profile.name, Project.name).all()

    results = []
    for inv in invoices:
        # Calculate net sales (sum of line totals)
        net_sales = sum(
            (li.unit_price or 0) * li.qty_fulfilled_this_invoice
            for li in inv.line_items
        )

        hst_amount = net_sales * (hst_rate / 100)

        results.append(InvoiceSummaryItem(
            invoice_id=inv.id,
            invoice_date=inv.created_at,
            uca_project_number=inv.quote.project.uca_project_number,
            project_name=inv.quote.project.name,
            customer_name=inv.quote.project.customer.name,
            client_po_number=inv.quote.client_po_number,
            net_sales=net_sales,
            hst_amount=hst_amount,
            grand_total=net_sales + hst_amount,
        ))

    return results


def get_line_item_description_for_invoice(item: QuoteLineItem, db: Session) -> str:
    """Get a human-readable description for a line item."""
    if item.item_type == "labor" and item.labor_id:
        labor = db.query(Labor).filter(Labor.id == item.labor_id).first()
        return labor.description if labor else "Labor"
    elif item.item_type == "part" and item.part_id:
        part = db.query(Part).filter(Part.id == item.part_id).first()
        return part.part_number if part else "Part"
    elif item.item_type == "misc":
        if item.misc_id:
            misc = db.query(Miscellaneous).filter(Miscellaneous.id == item.misc_id).first()
            return misc.description if misc else "Misc"
        return item.description or "Misc"
    return "Unknown item"


@router.get("/{invoice_id}", response_model=InvoiceSchema)
def get_invoice(invoice_id: int, db: Session = Depends(get_db)):
    """Get a single invoice with all line items."""
    invoice = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.line_items),
            joinedload(Invoice.quote).joinedload(Quote.project),
        )
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return populate_invoice_number(invoice, db)


@router.put("/{invoice_id}", response_model=InvoiceSchema)
def update_invoice_status(
    invoice_id: int,
    status_update: InvoiceStatusUpdate,
    db: Session = Depends(get_db)
):
    """Update invoice status (Sent → Paid only). Cannot change Voided invoices."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Cannot modify voided invoices
    if invoice.status == "Voided":
        raise HTTPException(status_code=400, detail="Cannot modify voided invoices")

    # Validate status transition
    valid_statuses = ["Sent", "Paid"]
    if status_update.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Status must be one of: {', '.join(valid_statuses)}"
        )

    # Can only go from Sent → Paid
    if invoice.status == "Paid" and status_update.status == "Sent":
        raise HTTPException(status_code=400, detail="Cannot change status from Paid to Sent")

    invoice.status = status_update.status
    db.commit()
    db.refresh(invoice)

    # Reload with relationships
    invoice = (
        db.query(Invoice)
        .options(
            joinedload(Invoice.line_items),
            joinedload(Invoice.quote).joinedload(Quote.project),
        )
        .filter(Invoice.id == invoice_id)
        .first()
    )
    return populate_invoice_number(invoice, db)


@router.put("/{invoice_id}/created-at", response_model=InvoiceSchema)
def update_invoice_created_at(invoice_id: int, payload: CreatedAtUpdate, db: Session = Depends(get_db)):
    """
    Edit the 'created on' date/time of an invoice.

    Bumps current_version by 1 and records the change in the invoice audit trail. There
    is NO frozen guard: an invoice only exists once its quote has been frozen, so the
    parent quote is always frozen and the invoice date must stay editable.

    invoice_number does NOT embed the invoice's own version, so bumping current_version
    here does not change the visible invoice number.
    """
    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.line_items), joinedload(Invoice.quote).joinedload(Quote.project))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    old_created_at = invoice.created_at
    invoice.created_at = to_naive_utc(payload.created_at)

    create_invoice_snapshot(
        db,
        invoice,
        action_type="date_edit",
        action_description=f"Created date changed from {old_created_at} to {invoice.created_at}",
    )

    db.commit()

    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.line_items), joinedload(Invoice.quote).joinedload(Quote.project))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    return populate_invoice_number(invoice, db)


# ==================== Snapshots (audit trail) ====================

@router.get("/{invoice_id}/snapshots", response_model=List[InvoiceSnapshotSchema])
def get_invoice_snapshots(invoice_id: int, db: Session = Depends(get_db)):
    """Get all snapshots (audit trail) for an invoice, newest first."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    snapshots = (
        db.query(InvoiceSnapshot)
        .options(joinedload(InvoiceSnapshot.line_item_states))
        .filter(InvoiceSnapshot.invoice_id == invoice_id)
        .order_by(InvoiceSnapshot.version.desc())
        .all()
    )
    return snapshots


@router.get("/{invoice_id}/snapshots/{version}", response_model=InvoiceSnapshotSchema)
def get_invoice_snapshot(invoice_id: int, version: int, db: Session = Depends(get_db)):
    """Get a specific invoice snapshot by version."""
    snapshot = (
        db.query(InvoiceSnapshot)
        .options(joinedload(InvoiceSnapshot.line_item_states))
        .filter(InvoiceSnapshot.invoice_id == invoice_id, InvoiceSnapshot.version == version)
        .first()
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snapshot


# ==================== Revert ====================

@router.get("/{invoice_id}/revert/{version}/preview", response_model=InvoiceRevertPreview)
def preview_invoice_revert(invoice_id: int, version: int, db: Session = Depends(get_db)):
    """Preview what would happen if we revert the invoice to a specific version."""
    invoice = db.query(Invoice).filter(Invoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    target_snapshot = (
        db.query(InvoiceSnapshot)
        .filter(InvoiceSnapshot.invoice_id == invoice_id, InvoiceSnapshot.version == version)
        .first()
    )
    if not target_snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    if target_snapshot.entity_created_at is not None:
        summary = f"Reverting will restore the created date to {target_snapshot.entity_created_at} and the line items as of version {version}."
    else:
        summary = f"Reverting will restore the line items as of version {version}."

    return InvoiceRevertPreview(target_version=version, changes_summary=summary)


@router.post("/{invoice_id}/revert/{version}", response_model=InvoiceSchema)
def revert_invoice_to_snapshot(invoice_id: int, version: int, db: Session = Depends(get_db)):
    """
    Revert the invoice to a specific snapshot version.
    - Restores all line items to their state at that version
    - Restores created_at from the snapshot's entity_created_at
    - Creates a new snapshot recording the revert action
    """
    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.line_items), joinedload(Invoice.quote).joinedload(Quote.project))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    target_snapshot = (
        db.query(InvoiceSnapshot)
        .options(joinedload(InvoiceSnapshot.line_item_states))
        .filter(InvoiceSnapshot.invoice_id == invoice_id, InvoiceSnapshot.version == version)
        .first()
    )
    if not target_snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    if version >= invoice.current_version:
        raise HTTPException(status_code=400, detail="Cannot revert to current or future version")

    # Restore created_at from the snapshot (entity_created_at captured at snapshot time)
    if target_snapshot.entity_created_at is not None:
        invoice.created_at = target_snapshot.entity_created_at

    # Replace current line items with the snapshot's line items
    db.query(InvoiceLineItem).filter(InvoiceLineItem.invoice_id == invoice_id).delete()

    for item_state in target_snapshot.line_item_states:
        db.add(InvoiceLineItem(
            invoice_id=invoice_id,
            quote_line_item_id=item_state.quote_line_item_id,
            item_type=item_state.item_type,
            description=item_state.description,
            unit_price=item_state.unit_price,
            qty_ordered=item_state.qty_ordered,
            qty_fulfilled_this_invoice=item_state.qty_fulfilled_this_invoice,
            qty_fulfilled_total=item_state.qty_fulfilled_total,
            qty_pending_after=item_state.qty_pending_after,
            labor_id=item_state.labor_id,
            part_id=item_state.part_id,
            misc_id=item_state.misc_id,
        ))

    db.flush()

    # Snapshot the revert action (re-captures the now-restored state)
    create_invoice_snapshot(
        db,
        invoice,
        action_type="revert",
        action_description=f"Reverted to version {version}",
    )

    db.commit()

    invoice = (
        db.query(Invoice)
        .options(joinedload(Invoice.line_items), joinedload(Invoice.quote).joinedload(Quote.project))
        .filter(Invoice.id == invoice_id)
        .first()
    )
    return populate_invoice_number(invoice, db)


# Invoice creation endpoint on quotes router (will be added to quotes.py)
# POST /quotes/{quote_id}/invoices - handled in quotes.py
