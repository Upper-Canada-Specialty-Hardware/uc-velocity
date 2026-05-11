from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import CostCode, Quote, PurchaseOrder
from schemas import (
    CostCodeCreate, CostCodeUpdate, CostCode as CostCodeSchema
)

router = APIRouter(prefix="/cost-codes", tags=["cost-codes"])

_CACHE_CONTROL = "private, max-age=60"


@router.get("/", response_model=List[CostCodeSchema])
def get_all_cost_codes(response: Response, db: Session = Depends(get_db)):
    """Get all cost codes, ordered by code."""
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return db.query(CostCode).order_by(CostCode.code).all()


@router.get("/{cost_code_id}", response_model=CostCodeSchema)
def get_cost_code(cost_code_id: int, response: Response, db: Session = Depends(get_db)):
    """Get a single cost code."""
    cc = db.query(CostCode).filter(CostCode.id == cost_code_id).first()
    if not cc:
        raise HTTPException(status_code=404, detail="Cost code not found")
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return cc


@router.post("/", response_model=CostCodeSchema)
def create_cost_code(data: CostCodeCreate, db: Session = Depends(get_db)):
    """Create a new cost code."""
    existing = db.query(CostCode).filter(CostCode.code == data.code).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Cost code '{data.code}' already exists")

    cc = CostCode(
        code=data.code,
        description=data.description,
        gp_cost_code_properties=data.gp_cost_code_properties,
        uch_dept_properties=data.uch_dept_properties,
    )
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return cc


@router.put("/{cost_code_id}", response_model=CostCodeSchema)
def update_cost_code(cost_code_id: int, data: CostCodeUpdate, db: Session = Depends(get_db)):
    """Update an existing cost code."""
    cc = db.query(CostCode).filter(CostCode.id == cost_code_id).first()
    if not cc:
        raise HTTPException(status_code=404, detail="Cost code not found")

    # Check unique code if being changed
    if data.code is not None and data.code != cc.code:
        existing = db.query(CostCode).filter(CostCode.code == data.code).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Cost code '{data.code}' already exists")
        cc.code = data.code

    if data.description is not None:
        cc.description = data.description
    if data.gp_cost_code_properties is not None:
        cc.gp_cost_code_properties = data.gp_cost_code_properties
    if data.uch_dept_properties is not None:
        cc.uch_dept_properties = data.uch_dept_properties

    db.commit()
    db.refresh(cc)
    return cc


@router.delete("/{cost_code_id}")
def delete_cost_code(cost_code_id: int, db: Session = Depends(get_db)):
    """Delete a cost code. Blocked if referenced by any Quote or PO."""
    cc = db.query(CostCode).filter(CostCode.id == cost_code_id).first()
    if not cc:
        raise HTTPException(status_code=404, detail="Cost code not found")

    # Check if referenced by any quotes
    quote_count = db.query(Quote).filter(Quote.cost_code_id == cost_code_id).count()
    if quote_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete cost code '{cc.code}' — it is used by {quote_count} quote(s). Reassign them first."
        )

    # Check if referenced by any purchase orders
    po_count = db.query(PurchaseOrder).filter(PurchaseOrder.cost_code_id == cost_code_id).count()
    if po_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete cost code '{cc.code}' — it is used by {po_count} purchase order(s). Reassign them first."
        )

    db.delete(cc)
    db.commit()
    return {"message": f"Cost code '{cc.code}' deleted successfully"}
