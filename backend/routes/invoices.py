from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from datetime import datetime, date

from database import get_db
from models import (
    Quote, QuoteLineItem, Invoice, InvoiceLineItem,
    QuoteSnapshot, QuoteLineItemSnapshot, Labor, Part, Miscellaneous,
    Project, Profile, CompanySettings
)
from schemas import (
    Invoice as InvoiceSchema,
    InvoiceCreate,
    InvoiceStatusUpdate,
    LineItemFulfillment,
    InvoiceSummaryItem
)
from routes.quotes import populate_invoice_number

router = APIRouter(prefix="/invoices", tags=["invoices"])


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


# Invoice creation endpoint on quotes router (will be added to quotes.py)
# POST /quotes/{quote_id}/invoices - handled in quotes.py
