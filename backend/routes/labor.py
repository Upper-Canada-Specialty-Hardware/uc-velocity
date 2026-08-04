from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import Labor
from schemas import LaborCreate, LaborUpdate, Labor as LaborSchema

router = APIRouter(prefix="/labor", tags=["labor"])


@router.get("/", response_model=List[LaborSchema])
def get_all_labor(skip: int = 0, limit: int = None, db: Session = Depends(get_db)):  # limit=None until pagination is implemented
    """Get all labor items with pagination."""
    labor_items = db.query(Labor).offset(skip).limit(limit).all()
    return labor_items


@router.get("/{labor_id}", response_model=LaborSchema)
def get_labor(labor_id: int, db: Session = Depends(get_db)):
    """Get a single labor item by ID."""
    labor = db.query(Labor).filter(Labor.id == labor_id).first()
    if not labor:
        raise HTTPException(status_code=404, detail="Labor not found")
    return labor


@router.post("/", response_model=LaborSchema)
def create_labor(labor_data: LaborCreate, db: Session = Depends(get_db)):
    """Create a new Labor entry."""
    # Round markup_percent to 2 decimal places
    formatted_markup = round(labor_data.markup_percent, 2)

    db_labor = Labor(
        description=labor_data.description,
        product_code=labor_data.product_code,
        hours=labor_data.hours,
        rate=labor_data.rate,
        markup_percent=formatted_markup,
        category_id=labor_data.category_id
    )

    db.add(db_labor)
    db.commit()
    db.refresh(db_labor)

    return db_labor


@router.put("/{labor_id}", response_model=LaborSchema)
def update_labor(labor_id: int, labor_data: LaborUpdate, db: Session = Depends(get_db)):
    """Update a labor item."""
    db_labor = db.query(Labor).filter(Labor.id == labor_id).first()
    if not db_labor:
        raise HTTPException(status_code=404, detail="Labor not found")

    # Update fields if provided
    if labor_data.description is not None:
        db_labor.description = labor_data.description
    if labor_data.product_code is not None:
        db_labor.product_code = labor_data.product_code
    if labor_data.hours is not None:
        db_labor.hours = labor_data.hours
    if labor_data.rate is not None:
        db_labor.rate = labor_data.rate
    if labor_data.markup_percent is not None:
        db_labor.markup_percent = round(labor_data.markup_percent, 2)
    if labor_data.category_id is not None:
        db_labor.category_id = labor_data.category_id

    db.commit()
    db.refresh(db_labor)
    return db_labor


@router.delete("/{labor_id}")
def delete_labor(labor_id: int, db: Session = Depends(get_db)):
    """Delete a labor item."""
    db_labor = db.query(Labor).filter(Labor.id == labor_id).first()
    if not db_labor:
        raise HTTPException(status_code=404, detail="Labor not found")

    db.delete(db_labor)
    db.commit()
    return {"message": "Labor deleted successfully"}
