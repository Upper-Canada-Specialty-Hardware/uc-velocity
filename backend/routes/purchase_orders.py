from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime

from database import get_db
from auth import current_actor
from models import (
    PurchaseOrder, POLineItem, Project, Profile, ProfileType, Part,
    POReceiving, POReceivingLineItem, POSnapshot, POLineItemSnapshot, POStatus, CostCode
)
from schemas import (
    Paginated,
    PurchaseOrderCreate, PurchaseOrderUpdate, PurchaseOrder as PurchaseOrderSchema,
    POLineItemCreate, POLineItem as POLineItemSchema,
    POReceiving as POReceivingSchema, POReceivingCreate, POReceivingLineItemCreate,
    POReceivingLineItem as POReceivingLineItemSchema,
    POSnapshot as POSnapshotSchema, POLineItemSnapshot as POLineItemSnapshotSchema,
    PORevertPreview, POCommitEditsRequest, POCommitEditsResponse, StagedPOLineItemChange
)

DEFAULT_LIMIT = 50
MAX_LIMIT = 10000


def create_po_snapshot(
    db: Session,
    po: PurchaseOrder,
    action_type: str,
    action_description: str,
    receiving_id: Optional[int] = None,
    deleted_line_item: Optional[POLineItem] = None
) -> POSnapshot:
    """
    Create a snapshot of the current PO state.

    Args:
        db: Database session
        po: The purchase order to snapshot
        action_type: Type of action ("create", "edit", "delete", "receive", "status_change", "revert")
        action_description: Human-readable description of the action
        receiving_id: Optional receiving ID if action_type is "receive"
        deleted_line_item: Optional line item being deleted (marked as is_deleted=True in snapshot)

    Returns:
        The created POSnapshot
    """
    # Increment version for substantive changes only
    # Version stays unchanged for: initial creation, status changes
    # Version increments for: edit, delete, receive, revert (substantive changes)
    if action_type in ["create", "status_change"]:
        # Keep version unchanged - these don't affect PO number
        new_version = po.current_version
    else:
        # Increment version for substantive changes to line items or receivings
        new_version = po.current_version + 1
        po.current_version = new_version

    # Create the snapshot (record the acting user from the request context)
    actor = current_actor.get()
    snapshot = POSnapshot(
        purchase_order_id=po.id,
        version=new_version,
        action_type=action_type,
        action_description=action_description,
        receiving_id=receiving_id,
        actor_user_id=actor["user_id"] if actor else None,
        actor_email=actor.get("email") if actor else None,
    )
    db.add(snapshot)
    db.flush()  # Get the snapshot ID

    # Snapshot all current line items
    line_items = (
        db.query(POLineItem)
        .filter(POLineItem.purchase_order_id == po.id)
        .all()
    )

    for item in line_items:
        # Skip the deleted line item - it will be added separately with is_deleted=True
        if deleted_line_item and item.id == deleted_line_item.id:
            continue

        item_snapshot = POLineItemSnapshot(
            snapshot_id=snapshot.id,
            original_line_item_id=item.id,
            item_type=item.item_type,
            part_id=item.part_id,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            qty_pending=item.qty_pending,
            qty_received=item.qty_received,
            actual_unit_price=item.actual_unit_price,
            is_deleted=False
        )
        db.add(item_snapshot)

    # If a line item is being deleted, include it in the snapshot with is_deleted=True
    if deleted_line_item:
        deleted_snapshot = POLineItemSnapshot(
            snapshot_id=snapshot.id,
            original_line_item_id=deleted_line_item.id,
            item_type=deleted_line_item.item_type,
            part_id=deleted_line_item.part_id,
            description=deleted_line_item.description,
            quantity=deleted_line_item.quantity,
            unit_price=deleted_line_item.unit_price,
            qty_pending=deleted_line_item.qty_pending,
            qty_received=deleted_line_item.qty_received,
            actual_unit_price=deleted_line_item.actual_unit_price,
            is_deleted=True
        )
        db.add(deleted_snapshot)

    return snapshot


def get_next_po_sequence(db: Session, project_id: int) -> int:
    """
    Get the next PO sequence number for a project.

    This function should be called within a transaction where the project
    row is locked (using with_for_update()) to prevent race conditions.

    Args:
        db: Database session
        project_id: The project ID to get next sequence for

    Returns:
        The next sequence number (1-based, increments from max existing)
    """
    max_seq = db.query(func.max(PurchaseOrder.po_sequence)).filter(
        PurchaseOrder.project_id == project_id
    ).scalar()
    return (max_seq or 0) + 1


def format_po_number(uca_project_number: str, po_sequence: int, current_version: int) -> str:
    """
    Format the full PO number string.

    Format: PO-{UCA Project Number}-{Sequence:04d}-{Version}
    Example: PO-A2132-0001-0, PO-A2132-0001-10

    Args:
        uca_project_number: The project's UCA number (e.g., "A2132")
        po_sequence: The per-project sequence number (1, 2, 3...)
        current_version: The audit trail version (0, 1, 2, 10...)

    Returns:
        Formatted PO number string
    """
    return f"PO-{uca_project_number}-{po_sequence:04d}-{current_version}"


def populate_po_number(po: PurchaseOrder, uca_project_number: str) -> PurchaseOrderSchema:
    """
    Convert a PurchaseOrder ORM object to a PurchaseOrderSchema with computed po_number.

    Args:
        po: The PurchaseOrder ORM object
        uca_project_number: The project's UCA number

    Returns:
        PurchaseOrderSchema with po_number populated
    """
    response = PurchaseOrderSchema.model_validate(po)
    response.po_number = format_po_number(
        uca_project_number,
        po.po_sequence,
        po.current_version
    )
    return response


def check_po_editable(po_id: int, db: Session) -> None:
    """
    Check if a PO is in Draft status (editable) and raise 400 if not.

    POs can only be edited when in Draft status. Once status becomes Sent,
    line items are locked and only receiving operations are allowed.

    Args:
        po_id: The PO ID to check
        db: Database session

    Raises:
        HTTPException: 400 error if PO is not in Draft status
    """
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    if po.status != POStatus.draft:
        raise HTTPException(
            status_code=400,
            detail=f"This PO has status '{po.status.value}' and cannot be edited. Only Draft POs can be modified."
        )


def calculate_weighted_average_price(line_item: POLineItem, db: Session) -> Optional[float]:
    """
    Calculate weighted average actual price from all non-voided receiving history.

    Formula: Sum(qty_received * actual_price) / Sum(qty_received)

    Args:
        line_item: The PO line item to calculate for
        db: Database session

    Returns:
        Weighted average price, or None if no receivings exist
    """
    # Get all non-voided receiving line items for this PO line item
    receiving_items = (
        db.query(POReceivingLineItem)
        .join(POReceiving)
        .filter(
            POReceivingLineItem.po_line_item_id == line_item.id,
            POReceiving.voided_at.is_(None)  # Exclude voided receivings
        )
        .all()
    )

    if not receiving_items:
        return None

    total_cost = 0.0
    total_qty = 0

    for item in receiving_items:
        if item.actual_unit_price is not None and item.qty_received_this_receiving:
            total_cost += item.actual_unit_price * item.qty_received_this_receiving
            total_qty += item.qty_received_this_receiving

    if total_qty == 0:
        return None

    return total_cost / total_qty


def recompute_line_item_aggregates(db: Session, line_item: POLineItem) -> None:
    """
    Recompute qty_received, qty_pending, and actual_unit_price from non-voided receiving history.

    This function is called after revert operations to recalculate aggregates
    based on remaining valid receivings.

    Args:
        db: Database session
        line_item: The PO line item to recompute
    """
    # Get all non-voided receiving line items
    receiving_items = (
        db.query(POReceivingLineItem)
        .join(POReceiving)
        .filter(
            POReceivingLineItem.po_line_item_id == line_item.id,
            POReceiving.voided_at.is_(None)
        )
        .all()
    )

    # Sum up quantities from non-voided receivings
    total_received = sum(item.qty_received_this_receiving or 0 for item in receiving_items)

    # Update line item aggregates
    line_item.qty_received = total_received
    line_item.qty_pending = max(0, line_item.quantity - total_received)

    # Recalculate weighted average price
    line_item.actual_unit_price = calculate_weighted_average_price(line_item, db)


router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


@router.get("/", response_model=Paginated[PurchaseOrderSchema])
def get_all_purchase_orders(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    skip: int = Query(0, ge=0, deprecated=True, description="Deprecated alias for offset"),
    db: Session = Depends(get_db),
):
    """Paginated list of purchase orders.

    Pass `limit=0` to fetch every row.
    """
    effective_offset = offset or skip
    base = db.query(PurchaseOrder).options(
        joinedload(PurchaseOrder.vendor),
        joinedload(PurchaseOrder.line_items),
        joinedload(PurchaseOrder.project),
        joinedload(PurchaseOrder.cost_code),
    )
    total = base.with_entities(PurchaseOrder.id).count()
    q = base.order_by(PurchaseOrder.id).offset(effective_offset)
    if limit > 0:
        q = q.limit(limit)
    pos = q.all()
    items = [populate_po_number(po, po.project.uca_project_number) for po in pos]
    return Paginated[PurchaseOrderSchema](
        items=items, total=total, limit=limit, offset=effective_offset
    )


@router.get("/{po_id}", response_model=PurchaseOrderSchema)
def get_purchase_order(po_id: int, db: Session = Depends(get_db)):
    """Get a single purchase order with line items."""
    po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.line_items).joinedload(POLineItem.part),
            joinedload(PurchaseOrder.project),
            joinedload(PurchaseOrder.cost_code)
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return populate_po_number(po, po.project.uca_project_number)


@router.post("/", response_model=PurchaseOrderSchema)
def create_purchase_order(po_data: PurchaseOrderCreate, db: Session = Depends(get_db)):
    """Create a new purchase order for a project."""
    # Verify project exists and lock row for sequence generation
    project = db.query(Project).filter(Project.id == po_data.project_id).with_for_update().first()
    if not project:
        raise HTTPException(status_code=400, detail="Project not found")

    # Verify vendor exists and is of type VENDOR
    vendor = db.query(Profile).filter(Profile.id == po_data.vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=400, detail="Vendor not found")
    if vendor.type != ProfileType.vendor:
        raise HTTPException(status_code=400, detail="Profile must be of type 'vendor'")

    # Get next sequence number
    next_sequence = get_next_po_sequence(db, po_data.project_id)

    # Resolve cost_code_id: use provided or default to "200-000"
    cost_code_id = po_data.cost_code_id
    if cost_code_id is None:
        default_cc = db.query(CostCode).filter(CostCode.code == "200-000").first()
        if default_cc:
            cost_code_id = default_cc.id

    db_po = PurchaseOrder(
        project_id=po_data.project_id,
        vendor_id=po_data.vendor_id,
        po_sequence=next_sequence,
        status=po_data.status,
        work_description=po_data.work_description,
        vendor_po_number=po_data.vendor_po_number,
        expected_delivery_date=po_data.expected_delivery_date,
        cost_code_id=cost_code_id
    )
    db.add(db_po)
    db.flush()  # Get the PO ID without committing

    # Create initial snapshot within same transaction
    create_po_snapshot(db, db_po, "create", "Purchase order created")

    # Commit both PO and snapshot together
    db.commit()
    db.refresh(db_po)

    # Reload with vendor, project, and cost_code relationships
    db_po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.project),
            joinedload(PurchaseOrder.cost_code)
        )
        .filter(PurchaseOrder.id == db_po.id)
        .first()
    )
    return populate_po_number(db_po, project.uca_project_number)


@router.put("/{po_id}", response_model=PurchaseOrderSchema)
def update_purchase_order(
    po_id: int,
    po_data: PurchaseOrderUpdate,
    db: Session = Depends(get_db)
):
    """Update purchase order metadata and status."""
    db_po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not db_po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Track if status changed
    status_changed = False
    old_status = db_po.status

    # Update fields if provided
    if po_data.status is not None and po_data.status != db_po.status:
        db_po.status = po_data.status
        status_changed = True

    if po_data.work_description is not None:
        db_po.work_description = po_data.work_description

    if po_data.vendor_po_number is not None:
        db_po.vendor_po_number = po_data.vendor_po_number

    if po_data.expected_delivery_date is not None:
        db_po.expected_delivery_date = po_data.expected_delivery_date

    if po_data.cost_code_id is not None:
        db_po.cost_code_id = po_data.cost_code_id

    # Create snapshot if status changed
    if status_changed:
        create_po_snapshot(db, db_po, "status_change", f"Status changed from {old_status.value} to {po_data.status.value}")

    db.commit()
    db.refresh(db_po)

    # Reload with vendor, project, and cost_code relationships
    db_po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.project),
            joinedload(PurchaseOrder.cost_code)
        )
        .filter(PurchaseOrder.id == db_po.id)
        .first()
    )
    return populate_po_number(db_po, db_po.project.uca_project_number)


@router.delete("/{po_id}")
def delete_purchase_order(po_id: int, db: Session = Depends(get_db)):
    """Delete a purchase order and all its line items."""
    db_po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not db_po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    db.delete(db_po)
    db.commit()
    return {"message": "Purchase order deleted successfully"}


# ==================== Line Items ====================
# IMPORTANT: PO line items can only be "part" or "misc", NEVER "labor"

@router.get("/{po_id}/lines", response_model=List[POLineItemSchema])
def get_po_lines(po_id: int, db: Session = Depends(get_db)):
    """Get all line items for a purchase order."""
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    lines = (
        db.query(POLineItem)
        .options(joinedload(POLineItem.part))
        .filter(POLineItem.purchase_order_id == po_id)
        .all()
    )
    return lines


@router.post("/{po_id}/lines", response_model=POLineItemSchema)
def add_po_line(po_id: int, line_data: POLineItemCreate, db: Session = Depends(get_db)):
    """
    Add a line item to a purchase order.

    IMPORTANT: Purchase orders can only have 'part' or 'misc' line items.
    Labor items are NOT allowed on purchase orders.
    """
    # Check if PO is editable (Draft status)
    check_po_editable(po_id, db)

    # Verify PO exists
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # CRITICAL: Validate item_type - NO LABOR ALLOWED
    if line_data.item_type not in ["part", "misc"]:
        raise HTTPException(
            status_code=400,
            detail="Purchase order line items must be 'part' or 'misc'. Labor items are not allowed on purchase orders."
        )

    # Validate references based on item_type
    if line_data.item_type == "part":
        if not line_data.part_id:
            raise HTTPException(status_code=400, detail="part_id required for part line items")
        part = db.query(Part).filter(Part.id == line_data.part_id).first()
        if not part:
            raise HTTPException(status_code=400, detail="Part not found")

    elif line_data.item_type == "misc":
        if not line_data.description:
            raise HTTPException(status_code=400, detail="description required for misc line items")

    db_line = POLineItem(
        purchase_order_id=po_id,
        item_type=line_data.item_type,
        part_id=line_data.part_id,
        description=line_data.description,
        quantity=line_data.quantity,
        unit_price=line_data.unit_price,
        qty_pending=line_data.quantity
    )
    db.add(db_line)
    db.flush()

    # Create snapshot
    create_po_snapshot(db, po, "edit", f"Added line item: {line_data.description or 'Part'}")

    db.commit()
    db.refresh(db_line)
    return db_line


@router.put("/{po_id}/lines/{line_id}", response_model=POLineItemSchema)
def update_po_line(
    po_id: int,
    line_id: int,
    line_data: POLineItemCreate,
    db: Session = Depends(get_db)
):
    """Update a purchase order line item."""
    # Check if PO is editable (Draft status)
    check_po_editable(po_id, db)

    db_line = (
        db.query(POLineItem)
        .filter(POLineItem.id == line_id, POLineItem.purchase_order_id == po_id)
        .first()
    )
    if not db_line:
        raise HTTPException(status_code=404, detail="Line item not found")

    # CRITICAL: Validate item_type - NO LABOR ALLOWED
    if line_data.item_type not in ["part", "misc"]:
        raise HTTPException(
            status_code=400,
            detail="Purchase order line items must be 'part' or 'misc'. Labor items are not allowed."
        )

    # Validate references based on item_type
    if line_data.item_type == "part":
        if not line_data.part_id:
            raise HTTPException(status_code=400, detail="part_id required for part line items")
        part = db.query(Part).filter(Part.id == line_data.part_id).first()
        if not part:
            raise HTTPException(status_code=400, detail="Part not found")

    elif line_data.item_type == "misc":
        if not line_data.description:
            raise HTTPException(status_code=400, detail="description required for misc line items")

    db_line.item_type = line_data.item_type
    db_line.part_id = line_data.part_id
    db_line.description = line_data.description
    db_line.quantity = line_data.quantity
    db_line.unit_price = line_data.unit_price

    # Recompute qty_pending based on new quantity and existing qty_received
    db_line.qty_pending = max(0, db_line.quantity - db_line.qty_received)

    # Get PO for snapshot
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()

    # Create snapshot
    create_po_snapshot(db, po, "edit", f"Updated line item: {db_line.description or 'Part'}")

    db.commit()
    db.refresh(db_line)
    return db_line


@router.delete("/{po_id}/lines/{line_id}")
def delete_po_line(po_id: int, line_id: int, db: Session = Depends(get_db)):
    """Delete a line item from a purchase order."""
    # Check if PO is editable (Draft status)
    check_po_editable(po_id, db)

    db_line = (
        db.query(POLineItem)
        .filter(POLineItem.id == line_id, POLineItem.purchase_order_id == po_id)
        .first()
    )
    if not db_line:
        raise HTTPException(status_code=404, detail="Line item not found")

    # Store description before delete for snapshot
    description = db_line.description or 'Part'

    # Get PO for snapshot
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()

    # Create snapshot BEFORE deleting, passing the deleted line item
    create_po_snapshot(db, po, "delete", f"Deleted line item: {description}", deleted_line_item=db_line)

    # Now delete the line item after snapshot is created
    db.delete(db_line)

    db.commit()
    return {"message": "Line item deleted successfully"}


# ==================== Batch Commit ====================

@router.post("/{po_id}/commit", response_model=POCommitEditsResponse)
def commit_po_edits(po_id: int, request: POCommitEditsRequest, db: Session = Depends(get_db)):
    """
    Commit a batch of line item changes (deletes, edits, adds) atomically.

    This endpoint processes staged changes in order: deletes → edits → adds
    All changes are committed in a single transaction with one snapshot.
    """
    # Validate PO is in Draft status
    check_po_editable(po_id, db)

    # Fetch PO with project for uca_project_number
    po = (
        db.query(PurchaseOrder)
        .options(joinedload(PurchaseOrder.project))
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Separate changes by action type
    deletes = [c for c in request.changes if c.action == "delete"]
    edits = [c for c in request.changes if c.action == "edit"]
    adds = [c for c in request.changes if c.action == "add"]

    # Guard: if no changes, return early without incrementing version
    if not deletes and not edits and not adds:
        # Reload PO with all relationships for response
        po = (
            db.query(PurchaseOrder)
            .options(
                joinedload(PurchaseOrder.vendor),
                joinedload(PurchaseOrder.line_items).joinedload(POLineItem.part),
                joinedload(PurchaseOrder.project),
                joinedload(PurchaseOrder.cost_code)
            )
            .filter(PurchaseOrder.id == po_id)
            .first()
        )

        return POCommitEditsResponse(
            success=True,
            message="No changes to commit",
            purchase_order=populate_po_number(po, po.project.uca_project_number),
            snapshot_version=po.current_version
        )

    # Track changes for action description
    changes_summary = []

    # Process DELETES first
    if deletes:
        delete_ids = [d.line_item_id for d in deletes]
        lines_to_delete = (
            db.query(POLineItem)
            .filter(
                POLineItem.id.in_(delete_ids),
                POLineItem.purchase_order_id == po_id
            )
            .all()
        )

        if len(lines_to_delete) != len(delete_ids):
            raise HTTPException(
                status_code=400,
                detail="One or more line items to delete not found or do not belong to this PO"
            )

        for line in lines_to_delete:
            db.delete(line)

        changes_summary.append(f"Deleted {len(deletes)} item(s)")

    # Process EDITS
    if edits:
        for edit in edits:
            db_line = (
                db.query(POLineItem)
                .filter(
                    POLineItem.id == edit.line_item_id,
                    POLineItem.purchase_order_id == po_id
                )
                .first()
            )
            if not db_line:
                raise HTTPException(
                    status_code=400,
                    detail=f"Line item {edit.line_item_id} not found or does not belong to this PO"
                )

            # Fall back to existing item_type if not provided in edit
            effective_item_type = edit.item_type or db_line.item_type
            if effective_item_type not in ["part", "misc"]:
                raise HTTPException(
                    status_code=400,
                    detail="Purchase order line items must be 'part' or 'misc'"
                )

            # Validate references based on item_type
            effective_part_id = edit.part_id if edit.part_id is not None else db_line.part_id
            if effective_item_type == "part":
                if not effective_part_id:
                    raise HTTPException(status_code=400, detail="part_id required for part line items")
                part = db.query(Part).filter(Part.id == effective_part_id).first()
                if not part:
                    raise HTTPException(status_code=400, detail=f"Part {effective_part_id} not found")
            elif effective_item_type == "misc":
                if not edit.description and not db_line.description:
                    raise HTTPException(status_code=400, detail="description required for misc line items")

            # Validate quantity not reduced below qty_received
            if edit.quantity < db_line.qty_received:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot reduce quantity below already received amount ({db_line.qty_received})"
                )

            # Update fields (use effective_item_type which falls back to existing)
            db_line.item_type = effective_item_type
            db_line.part_id = edit.part_id if edit.part_id is not None else db_line.part_id
            db_line.description = edit.description if edit.description is not None else db_line.description
            db_line.quantity = edit.quantity
            db_line.unit_price = edit.unit_price

            # Recalculate qty_pending
            db_line.qty_pending = max(0, edit.quantity - db_line.qty_received)

        changes_summary.append(f"Edited {len(edits)} item(s)")

    # Process ADDS
    if adds:
        for add in adds:
            # Validate item_type
            if add.item_type not in ["part", "misc"]:
                raise HTTPException(
                    status_code=400,
                    detail="Purchase order line items must be 'part' or 'misc'"
                )

            # Validate references based on item_type
            if add.item_type == "part":
                if not add.part_id:
                    raise HTTPException(status_code=400, detail="part_id required for part line items")
                part = db.query(Part).filter(Part.id == add.part_id).first()
                if not part:
                    raise HTTPException(status_code=400, detail=f"Part {add.part_id} not found")
            elif add.item_type == "misc":
                if not add.description:
                    raise HTTPException(status_code=400, detail="description required for misc line items")

            # Validate quantity is positive
            if add.quantity <= 0:
                raise HTTPException(status_code=400, detail="Quantity must be positive")

            # Create new line item
            db_line = POLineItem(
                purchase_order_id=po_id,
                item_type=add.item_type,
                part_id=add.part_id,
                description=add.description,
                quantity=add.quantity,
                unit_price=add.unit_price,
                qty_pending=add.quantity,
                qty_received=0
            )
            db.add(db_line)

        changes_summary.append(f"Added {len(adds)} item(s)")

    # Build action description
    action_description = "Batch edit: " + ", ".join(changes_summary) if changes_summary else "No changes"

    # Create single snapshot for all changes
    create_po_snapshot(db, po, "edit", action_description)

    # Commit transaction atomically
    db.commit()

    # Reload PO with all relationships
    po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.line_items).joinedload(POLineItem.part),
            joinedload(PurchaseOrder.project),
            joinedload(PurchaseOrder.cost_code)
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )

    return POCommitEditsResponse(
        success=True,
        message=f"Successfully committed {len(request.changes)} changes",
        purchase_order=populate_po_number(po, po.project.uca_project_number),
        snapshot_version=po.current_version
    )


# ==================== Receiving ====================

@router.get("/{po_id}/receivings", response_model=List[POReceivingSchema])
def get_po_receivings(po_id: int, db: Session = Depends(get_db)):
    """
    List all receiving records for a purchase order.

    Returns receivings ordered by created_at descending (newest first).
    """
    # Verify PO exists
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Query receivings with line items
    receivings = (
        db.query(POReceiving)
        .options(joinedload(POReceiving.line_items))
        .filter(POReceiving.purchase_order_id == po_id)
        .order_by(POReceiving.created_at.desc())
        .all()
    )

    return receivings


@router.post("/{po_id}/receivings", response_model=POReceivingSchema)
def create_po_receiving(po_id: int, receiving_data: POReceivingCreate, db: Session = Depends(get_db)):
    """
    Create a receiving record for a purchase order.

    Updates line item aggregates and creates snapshot. Auto-transitions
    PO status to Received when all items are fully received.
    """
    # Fetch PO with line items and project
    po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.line_items),
            joinedload(PurchaseOrder.project)
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Validate PO status
    if po.status not in [POStatus.sent, POStatus.received]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot receive items for PO with status '{po.status.value}'. PO must be Sent or Received."
        )

    # Create receiving record
    receiving = POReceiving(
        purchase_order_id=po_id,
        received_date=receiving_data.received_date,
        notes=receiving_data.notes
    )
    db.add(receiving)
    db.flush()  # Get receiving ID

    # Track changes for action description
    received_items = []

    # Process each line item in the receiving
    for receiving_line_data in receiving_data.line_items:
        # Fetch corresponding PO line item
        line_item = (
            db.query(POLineItem)
            .filter(POLineItem.id == receiving_line_data.po_line_item_id)
            .first()
        )
        if not line_item:
            raise HTTPException(
                status_code=400,
                detail=f"Line item {receiving_line_data.po_line_item_id} not found"
            )

        # Validate line item belongs to this PO
        if line_item.purchase_order_id != po_id:
            raise HTTPException(
                status_code=400,
                detail=f"Line item {receiving_line_data.po_line_item_id} does not belong to this PO"
            )

        # Validate quantity
        if receiving_line_data.qty_received <= 0:
            raise HTTPException(
                status_code=400,
                detail="Received quantity must be positive"
            )

        if receiving_line_data.qty_received > line_item.qty_pending:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot receive {receiving_line_data.qty_received} units - only {line_item.qty_pending} pending"
            )

        # Create receiving line item with snapshot fields
        receiving_line_item = POReceivingLineItem(
            receiving_id=receiving.id,
            po_line_item_id=line_item.id,
            item_type=line_item.item_type,
            description=line_item.description,
            part_id=line_item.part_id,
            unit_price=line_item.unit_price,
            actual_unit_price=receiving_line_data.actual_unit_price if receiving_line_data.actual_unit_price is not None else line_item.unit_price,
            qty_ordered=line_item.quantity,
            qty_received_this_receiving=receiving_line_data.qty_received,
            qty_received_total=line_item.qty_received + receiving_line_data.qty_received,
            qty_pending_after=line_item.qty_pending - receiving_line_data.qty_received
        )
        db.add(receiving_line_item)
        db.flush()

        # Update PO line item aggregates
        line_item.qty_received += receiving_line_data.qty_received
        line_item.qty_pending -= receiving_line_data.qty_received

        # Recalculate weighted average actual price
        line_item.actual_unit_price = calculate_weighted_average_price(line_item, db)

        # Track for action description
        item_desc = line_item.description or f"Part {line_item.part_id}"
        received_items.append(f"{receiving_line_data.qty_received} units of {item_desc}")

    # Build action description
    action_description = "Received: " + ", ".join(received_items)

    # Create snapshot for receiving
    create_po_snapshot(db, po, "receive", action_description, receiving_id=receiving.id)

    # Check if all line items are fully received
    all_received = all(item.qty_pending == 0 for item in po.line_items)
    if all_received and po.status != POStatus.received:
        # Auto-transition to Received status
        old_status = po.status
        po.status = POStatus.received

        # Create additional snapshot for status change
        create_po_snapshot(
            db, po, "status_change",
            f"Status auto-changed from {old_status.value} to {POStatus.received.value} (all items received)"
        )

    # Commit transaction
    db.commit()

    # Reload receiving with line items
    receiving = (
        db.query(POReceiving)
        .options(joinedload(POReceiving.line_items))
        .filter(POReceiving.id == receiving.id)
        .first()
    )

    return receiving


# ==================== Snapshots ====================

@router.get("/{po_id}/snapshots", response_model=List[POSnapshotSchema])
def get_po_snapshots(po_id: int, db: Session = Depends(get_db)):
    """
    List all snapshots for a purchase order.

    Returns snapshots ordered by version descending (newest first).
    """
    # Verify PO exists
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Query snapshots with line item states
    snapshots = (
        db.query(POSnapshot)
        .options(joinedload(POSnapshot.line_item_states))
        .filter(POSnapshot.purchase_order_id == po_id)
        .order_by(POSnapshot.version.desc())
        .all()
    )

    return snapshots


@router.get("/{po_id}/snapshots/{version}", response_model=POSnapshotSchema)
def get_po_snapshot_version(po_id: int, version: int, db: Session = Depends(get_db)):
    """
    Get a specific snapshot version for a purchase order.
    """
    # Verify PO exists
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Query specific snapshot
    snapshot = (
        db.query(POSnapshot)
        .options(joinedload(POSnapshot.line_item_states))
        .filter(
            POSnapshot.purchase_order_id == po_id,
            POSnapshot.version == version
        )
        .first()
    )

    if not snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot version {version} not found for this purchase order"
        )

    return snapshot


# ==================== Revert ====================

@router.post("/{po_id}/revert/{version}/preview", response_model=PORevertPreview)
def preview_po_revert(po_id: int, version: int, db: Session = Depends(get_db)):
    """
    Preview the changes that will occur when reverting to a specific version.

    Shows which receivings will be voided and provides a summary of changes.
    """
    # Verify PO exists
    po = db.query(PurchaseOrder).filter(PurchaseOrder.id == po_id).first()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Validate version is not in the future
    if version > po.current_version:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot revert to future version {version} (current version is {po.current_version})"
        )

    # Fetch target snapshot
    target_snapshot = (
        db.query(POSnapshot)
        .options(joinedload(POSnapshot.line_item_states))
        .filter(
            POSnapshot.purchase_order_id == po_id,
            POSnapshot.version == version
        )
        .first()
    )

    if not target_snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot version {version} not found"
        )

    # Cannot revert to current version
    if version == po.current_version:
        raise HTTPException(
            status_code=400,
            detail="Cannot revert to current version"
        )

    # Query receivings that will be voided (created after target snapshot)
    receivings_to_void = (
        db.query(POReceiving)
        .join(POSnapshot, POSnapshot.receiving_id == POReceiving.id)
        .options(joinedload(POReceiving.line_items))
        .filter(
            POReceiving.purchase_order_id == po_id,
            POSnapshot.version > version,
            POReceiving.voided_at.is_(None)
        )
        .all()
    )

    # Build changes summary
    num_receivings = len(receivings_to_void)
    num_snapshot_items = len([li for li in target_snapshot.line_item_states if not li.is_deleted])

    changes_summary = (
        f"Reverting to version {version} will:\n"
        f"- Void {num_receivings} receiving record(s)\n"
        f"- Restore {num_snapshot_items} line item(s) to their state at version {version}\n"
        f"- Recompute quantities and prices from remaining non-voided receivings"
    )

    return PORevertPreview(
        target_version=version,
        receivings_to_void=receivings_to_void,
        changes_summary=changes_summary
    )


@router.post("/{po_id}/revert/{version}", response_model=PurchaseOrderSchema)
def revert_po_to_version(po_id: int, version: int, db: Session = Depends(get_db)):
    """
    Revert a purchase order to a specific version.

    This operation:
    1. Creates a new snapshot for the revert action
    2. Voids all future receiving records
    3. Restores line items from the target snapshot
    4. Recomputes aggregates from remaining non-voided receivings
    5. Updates PO status based on recomputed quantities
    """
    # Fetch PO with line items and project
    po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.project),
            joinedload(PurchaseOrder.line_items)
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Fetch target snapshot
    target_snapshot = (
        db.query(POSnapshot)
        .options(joinedload(POSnapshot.line_item_states))
        .filter(
            POSnapshot.purchase_order_id == po_id,
            POSnapshot.version == version
        )
        .first()
    )

    if not target_snapshot:
        raise HTTPException(
            status_code=404,
            detail=f"Snapshot version {version} not found"
        )

    # Validate version
    if version == po.current_version:
        raise HTTPException(
            status_code=400,
            detail="Cannot revert to current version"
        )

    if version > po.current_version:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot revert to future version {version} (current version is {po.current_version})"
        )

    # Query future receiving records that will be voided
    future_receivings = (
        db.query(POReceiving)
        .join(POSnapshot, POSnapshot.receiving_id == POReceiving.id)
        .filter(
            POReceiving.purchase_order_id == po_id,
            POSnapshot.version > version,
            POReceiving.voided_at.is_(None)
        )
        .all()
    )

    # Void future receivings BEFORE recomputing aggregates
    # This ensures recompute_line_item_aggregates() excludes these voided receivings
    # Note: voided_by_snapshot_id will be set after snapshot is created
    for receiving in future_receivings:
        receiving.voided_at = datetime.utcnow()
        receiving.voided_by_snapshot_id = None  # Will be set after snapshot creation

    db.flush()  # Persist voiding before recomputation

    # Get existing line items and sort deterministically by ID
    existing_line_items = (
        db.query(POLineItem)
        .filter(POLineItem.purchase_order_id == po_id)
        .order_by(POLineItem.id)
        .all()
    )

    # Get snapshot line items (non-deleted) and sort deterministically by original_line_item_id
    snapshot_items = sorted(
        [item for item in target_snapshot.line_item_states if not item.is_deleted],
        key=lambda x: x.original_line_item_id
    )

    # Build mapping of existing line items by ID for quick lookup
    existing_by_id = {line.id: line for line in existing_line_items}

    # Track which existing line items were updated
    updated_line_ids = set()

    # Build mapping from snapshot original_line_item_id to restored POLineItem.id
    line_item_id_mapping = {}

    # Update existing line items in place or create new ones
    for item_snapshot in snapshot_items:
        # Try to find existing line item with matching ID
        existing_line = existing_by_id.get(item_snapshot.original_line_item_id)

        if existing_line:
            # Reuse existing line item - update in place
            existing_line.item_type = item_snapshot.item_type
            existing_line.part_id = item_snapshot.part_id
            existing_line.description = item_snapshot.description
            existing_line.quantity = item_snapshot.quantity
            existing_line.unit_price = item_snapshot.unit_price
            existing_line.qty_received = 0
            existing_line.qty_pending = item_snapshot.quantity
            existing_line.actual_unit_price = None

            # Map snapshot's original line item ID to the reused POLineItem ID
            line_item_id_mapping[item_snapshot.original_line_item_id] = existing_line.id
            updated_line_ids.add(existing_line.id)
        else:
            # Need to create new line item
            new_line = POLineItem(
                purchase_order_id=po_id,
                item_type=item_snapshot.item_type,
                part_id=item_snapshot.part_id,
                description=item_snapshot.description,
                quantity=item_snapshot.quantity,
                unit_price=item_snapshot.unit_price,
                qty_received=0,
                qty_pending=item_snapshot.quantity,
                actual_unit_price=None
            )
            db.add(new_line)
            db.flush()  # Get the new ID

            # Map snapshot's original line item ID to the new POLineItem ID
            line_item_id_mapping[item_snapshot.original_line_item_id] = new_line.id

    # Delete existing line items that were not updated (not in snapshot)
    for existing_line in existing_line_items:
        if existing_line.id not in updated_line_ids:
            db.delete(existing_line)

    db.flush()

    # Update POReceivingLineItem FK references to point to correct restored line items
    # This ensures non-voided receivings still reference valid line items
    for original_line_item_id, new_line_item_id in line_item_id_mapping.items():
        db.query(POReceivingLineItem).filter(
            POReceivingLineItem.po_line_item_id == original_line_item_id
        ).update(
            {"po_line_item_id": new_line_item_id},
            synchronize_session=False
        )

    db.flush()

    # Recompute aggregates for all restored line items
    restored_items = (
        db.query(POLineItem)
        .filter(POLineItem.purchase_order_id == po_id)
        .all()
    )

    for line_item in restored_items:
        recompute_line_item_aggregates(db, line_item)

    # Update PO status based on recomputed quantities
    if all(item.qty_pending == item.quantity for item in restored_items):
        # No receivings remain - revert to Draft
        po.status = POStatus.draft
    elif all(item.qty_pending == 0 for item in restored_items):
        # All items fully received
        po.status = POStatus.received
    else:
        # Partially received
        po.status = POStatus.sent

    # Create snapshot after revert operations to capture final restored state
    revert_snapshot = create_po_snapshot(db, po, "revert", f"Reverted to version {version}")
    db.flush()  # Get snapshot ID

    # Update voided receivings with the revert snapshot ID
    # (voided_at was already set earlier, before recomputation)
    for receiving in future_receivings:
        receiving.voided_by_snapshot_id = revert_snapshot.id

    # Commit transaction
    db.commit()

    # Reload PO with all relationships
    po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.line_items).joinedload(POLineItem.part),
            joinedload(PurchaseOrder.project),
            joinedload(PurchaseOrder.cost_code)
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )

    return populate_po_number(po, po.project.uca_project_number)


# ==================== Clone ====================

@router.post("/{po_id}/clone", response_model=PurchaseOrderSchema)
def clone_purchase_order(po_id: int, db: Session = Depends(get_db)):
    """
    Clone a purchase order with a new sequence number.

    Creates a new PO with:
    - Same project, vendor, and metadata
    - Next available sequence number
    - Version reset to 0
    - Status reset to Draft
    - All line items cloned with quantities reset
    - No receiving history
    """
    # Fetch source PO with line items and project
    source_po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.line_items),
            joinedload(PurchaseOrder.project)
        )
        .filter(PurchaseOrder.id == po_id)
        .first()
    )
    if not source_po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    # Lock project row for sequence generation
    project = (
        db.query(Project)
        .filter(Project.id == source_po.project_id)
        .with_for_update()
        .first()
    )

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Get next sequence number
    next_sequence = get_next_po_sequence(db, source_po.project_id)

    # Create new PO
    new_po = PurchaseOrder(
        project_id=source_po.project_id,
        vendor_id=source_po.vendor_id,
        po_sequence=next_sequence,
        current_version=0,
        status=POStatus.draft,
        work_description=source_po.work_description,
        vendor_po_number=source_po.vendor_po_number,
        expected_delivery_date=source_po.expected_delivery_date,
        cost_code_id=source_po.cost_code_id
    )
    db.add(new_po)
    db.flush()  # Get new PO ID

    # Clone all line items
    for source_line in source_po.line_items:
        cloned_line = POLineItem(
            purchase_order_id=new_po.id,
            item_type=source_line.item_type,
            part_id=source_line.part_id,
            description=source_line.description,
            quantity=source_line.quantity,
            unit_price=source_line.unit_price,
            qty_pending=source_line.quantity,
            qty_received=0,
            actual_unit_price=None
        )
        db.add(cloned_line)

    # Create initial snapshot
    create_po_snapshot(db, new_po, "create", "Purchase order cloned")

    # Commit transaction
    db.commit()

    # Reload new PO with all relationships
    new_po = (
        db.query(PurchaseOrder)
        .options(
            joinedload(PurchaseOrder.vendor),
            joinedload(PurchaseOrder.line_items).joinedload(POLineItem.part),
            joinedload(PurchaseOrder.project),
            joinedload(PurchaseOrder.cost_code)
        )
        .filter(PurchaseOrder.id == new_po.id)
        .first()
    )

    return populate_po_number(new_po, project.uca_project_number)
