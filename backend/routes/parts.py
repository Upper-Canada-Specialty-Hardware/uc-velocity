from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from database import get_db
from models import Part, Labor, Profile
from schemas import PartCreate, PartUpdate, Part as PartSchema, PartWithLabor, Paginated

router = APIRouter(prefix="/parts", tags=["parts"])

# Default page size for list endpoints (issue #84).
# `limit=0` is a sentinel meaning "no cap" — used by autocomplete/dropdown
# loaders that legitimately need every row (e.g. PartForm vendor select).
DEFAULT_LIMIT = 50
MAX_LIMIT = 10000


def auto_calculate_cost(part: Part, db: Session) -> None:
    """Auto-calculate cost from list_price and vendor discount when applicable."""
    if part.list_price is not None and part.vendor_id:
        vendor = db.query(Profile).filter(Profile.id == part.vendor_id).first()
        if vendor:
            effective_discount = (
                part.discount_percent
                if part.discount_percent is not None
                else (vendor.default_discount_percent or 0)
            )
            part.cost = part.list_price * (1 - effective_discount / 100)


@router.get("/", response_model=Paginated[PartWithLabor])
def get_all_parts(
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    vendor_id: Optional[int] = Query(None, description="Filter to parts linked to this vendor"),
    skip: int = Query(0, ge=0, deprecated=True, description="Deprecated alias for offset"),
    db: Session = Depends(get_db),
):
    """Paginated list of parts with their linked labor items.

    Optional `vendor_id` filters server-side to parts linked to that vendor.
    Pass `limit=0` to fetch every row (used by autocomplete loaders).
    """
    effective_offset = offset or skip
    base = db.query(Part).options(
        joinedload(Part.labor_items), joinedload(Part.vendor)
    )
    if vendor_id is not None:
        base = base.filter(Part.vendor_id == vendor_id)
    total = base.with_entities(Part.id).count()
    q = base.order_by(Part.id).offset(effective_offset)
    if limit > 0:
        q = q.limit(limit)
    items = q.all()
    return Paginated[PartWithLabor](
        items=items, total=total, limit=limit, offset=effective_offset
    )


@router.get("/{part_id}", response_model=PartWithLabor)
def get_part(part_id: int, db: Session = Depends(get_db)):
    """Get a single part by ID with linked labor items."""
    part = (
        db.query(Part)
        .options(joinedload(Part.labor_items), joinedload(Part.vendor))
        .filter(Part.id == part_id)
        .first()
    )
    if not part:
        raise HTTPException(status_code=404, detail="Part not found")
    return part


@router.post("/", response_model=PartWithLabor)
def create_part(part_data: PartCreate, db: Session = Depends(get_db)):
    """Create a new part with optional labor linking."""
    # Check for duplicate part number
    existing = db.query(Part).filter(Part.part_number == part_data.part_number).first()
    if existing:
        raise HTTPException(status_code=400, detail="Part number already exists")

    # Validate all labor IDs exist BEFORE creating part
    labor_to_link = []
    if part_data.linked_labor_ids:
        labor_to_link = db.query(Labor).filter(
            Labor.id.in_(part_data.linked_labor_ids)
        ).all()

        # Verify all requested labor items were found
        found_ids = {l.id for l in labor_to_link}
        missing_ids = set(part_data.linked_labor_ids) - found_ids
        if missing_ids:
            raise HTTPException(
                status_code=400,
                detail=f"Labor items not found: {list(missing_ids)}"
            )

    # Round markup_percent to 2 decimal places
    formatted_markup = round(part_data.markup_percent, 2) if part_data.markup_percent else 0.0

    # Validate vendor if provided
    if part_data.vendor_id:
        vendor = db.query(Profile).filter(Profile.id == part_data.vendor_id).first()
        if not vendor:
            raise HTTPException(status_code=400, detail="Vendor not found")

    db_part = Part(
        part_number=part_data.part_number,
        description=part_data.description,
        cost=part_data.cost,
        markup_percent=formatted_markup,
        category_id=part_data.category_id,
        vendor_id=part_data.vendor_id,
        list_price=part_data.list_price,
        discount_percent=part_data.discount_percent,
    )

    # Auto-calculate cost from list_price + vendor discount
    auto_calculate_cost(db_part, db)

    # Link the labor items
    db_part.labor_items = labor_to_link

    db.add(db_part)
    db.commit()
    db.refresh(db_part)
    return db_part


@router.put("/{part_id}", response_model=PartWithLabor)
def update_part(part_id: int, part_data: PartUpdate, db: Session = Depends(get_db)):
    """Update an existing part and its labor links."""
    db_part = db.query(Part).filter(Part.id == part_id).first()
    if not db_part:
        raise HTTPException(status_code=404, detail="Part not found")

    # Update fields if provided
    if part_data.part_number is not None:
        # Check for duplicate part number
        existing = db.query(Part).filter(
            Part.part_number == part_data.part_number,
            Part.id != part_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Part number already exists")
        db_part.part_number = part_data.part_number

    if part_data.description is not None:
        db_part.description = part_data.description
    if part_data.cost is not None:
        db_part.cost = part_data.cost
    if part_data.markup_percent is not None:
        db_part.markup_percent = round(part_data.markup_percent, 2)
    if part_data.category_id is not None:
        db_part.category_id = part_data.category_id
    if part_data.vendor_id is not None:
        vendor = db.query(Profile).filter(Profile.id == part_data.vendor_id).first()
        if not vendor:
            raise HTTPException(status_code=400, detail="Vendor not found")
        db_part.vendor_id = part_data.vendor_id
    if part_data.list_price is not None:
        db_part.list_price = part_data.list_price
    if part_data.discount_percent is not None:
        db_part.discount_percent = part_data.discount_percent

    # Auto-calculate cost from list_price + vendor discount
    auto_calculate_cost(db_part, db)

    # Update labor links if provided
    if part_data.linked_labor_ids is not None:
        if part_data.linked_labor_ids:
            labor_to_link = db.query(Labor).filter(
                Labor.id.in_(part_data.linked_labor_ids)
            ).all()

            # Verify all requested labor items were found
            found_ids = {l.id for l in labor_to_link}
            missing_ids = set(part_data.linked_labor_ids) - found_ids
            if missing_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Labor items not found: {list(missing_ids)}"
                )
            db_part.labor_items = labor_to_link
        else:
            db_part.labor_items = []

    db.commit()
    db.refresh(db_part)
    return db_part


@router.delete("/{part_id}")
def delete_part(part_id: int, db: Session = Depends(get_db)):
    """Delete a part."""
    db_part = db.query(Part).filter(Part.id == part_id).first()
    if not db_part:
        raise HTTPException(status_code=404, detail="Part not found")

    db.delete(db_part)
    db.commit()
    return {"message": "Part deleted successfully"}
