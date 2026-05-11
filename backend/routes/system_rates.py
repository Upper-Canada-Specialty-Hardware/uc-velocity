from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from typing import List

from database import get_db
from models import SystemRate, Miscellaneous, CompanySettings
from schemas import (
    SystemRate as SystemRateSchema,
    SystemRateCreate, SystemRateUpdate,
)

router = APIRouter(prefix="/system-rates", tags=["system-rates"])

_CACHE_CONTROL = "private, max-age=60"


def _sync_misc_shadow(db: Session, system_rate: SystemRate) -> None:
    """Sync the shadow miscellaneous record to match the system rate."""
    if system_rate.linked_misc_id:
        misc = db.query(Miscellaneous).filter(Miscellaneous.id == system_rate.linked_misc_id).first()
        if misc:
            misc.description = system_rate.description
            misc.unit_price = system_rate.unit_price
            misc.markup_percent = system_rate.markup_percent
    else:
        # Create a new shadow misc record
        misc = Miscellaneous(
            description=system_rate.description,
            unit_price=system_rate.unit_price,
            markup_percent=system_rate.markup_percent,
            is_system_item=True,
        )
        db.add(misc)
        db.flush()  # Get the ID
        system_rate.linked_misc_id = misc.id


# ===== Parking =====

@router.get("/parking", response_model=SystemRateSchema)
def get_parking_rate(response: Response, db: Session = Depends(get_db)):
    """Get the parking system rate."""
    rate = db.query(SystemRate).filter(
        SystemRate.rate_type == "parking",
        SystemRate.is_active == True,
    ).first()
    if not rate:
        raise HTTPException(status_code=404, detail="Parking rate not found")
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return rate


@router.put("/parking", response_model=SystemRateSchema)
def update_parking_rate(data: SystemRateUpdate, db: Session = Depends(get_db)):
    """Update the parking rate."""
    rate = db.query(SystemRate).filter(
        SystemRate.rate_type == "parking",
        SystemRate.is_active == True,
    ).first()
    if not rate:
        raise HTTPException(status_code=404, detail="Parking rate not found")

    if data.description is not None:
        rate.description = data.description
    if data.unit_price is not None:
        rate.unit_price = data.unit_price
    if data.markup_percent is not None:
        rate.markup_percent = data.markup_percent

    _sync_misc_shadow(db, rate)
    db.commit()
    db.refresh(rate)
    return rate


# ===== Travel Distance =====

@router.get("/travel-distance", response_model=List[SystemRateSchema])
def get_travel_distance_tiers(response: Response, db: Session = Depends(get_db)):
    """Get all active travel distance tiers, ordered by sort_order."""
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return db.query(SystemRate).filter(
        SystemRate.rate_type == "travel_distance",
        SystemRate.is_active == True,
    ).order_by(SystemRate.sort_order).all()


@router.post("/travel-distance", response_model=SystemRateSchema)
def create_travel_distance_tier(data: SystemRateCreate, db: Session = Depends(get_db)):
    """Add a new travel distance tier."""
    rate = SystemRate(
        rate_type="travel_distance",
        description=data.description,
        unit_price=data.unit_price,
        markup_percent=data.markup_percent,
        sort_order=data.sort_order,
        is_active=True,
    )
    db.add(rate)
    db.flush()

    _sync_misc_shadow(db, rate)
    db.commit()
    db.refresh(rate)
    return rate


@router.put("/travel-distance/{rate_id}", response_model=SystemRateSchema)
def update_travel_distance_tier(rate_id: int, data: SystemRateUpdate, db: Session = Depends(get_db)):
    """Update a travel distance tier."""
    rate = db.query(SystemRate).filter(
        SystemRate.id == rate_id,
        SystemRate.rate_type == "travel_distance",
    ).first()
    if not rate:
        raise HTTPException(status_code=404, detail="Travel distance tier not found")

    if data.description is not None:
        rate.description = data.description
    if data.unit_price is not None:
        rate.unit_price = data.unit_price
    if data.markup_percent is not None:
        rate.markup_percent = data.markup_percent
    if data.sort_order is not None:
        rate.sort_order = data.sort_order

    _sync_misc_shadow(db, rate)
    db.commit()
    db.refresh(rate)
    return rate


@router.delete("/travel-distance/{rate_id}", response_model=SystemRateSchema)
def delete_travel_distance_tier(rate_id: int, db: Session = Depends(get_db)):
    """Soft-delete a travel distance tier (is_active=false)."""
    rate = db.query(SystemRate).filter(
        SystemRate.id == rate_id,
        SystemRate.rate_type == "travel_distance",
    ).first()
    if not rate:
        raise HTTPException(status_code=404, detail="Travel distance tier not found")

    rate.is_active = False
    # Keep the misc record — existing line items may reference it
    db.commit()
    db.refresh(rate)
    return rate


# ===== PMS Default =====

@router.get("/pms-default")
def get_pms_default(response: Response, db: Session = Depends(get_db)):
    """Get the default PMS percentage."""
    settings = db.query(CompanySettings).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Company settings not found")
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return {"default_pms_percent": settings.default_pms_percent}


@router.put("/pms-default")
def update_pms_default(data: dict, db: Session = Depends(get_db)):
    """Update the default PMS percentage."""
    settings = db.query(CompanySettings).first()
    if not settings:
        raise HTTPException(status_code=404, detail="Company settings not found")

    pms_val = data.get("default_pms_percent")
    if pms_val is not None and (not isinstance(pms_val, (int, float)) or pms_val < 0):
        raise HTTPException(status_code=422, detail="default_pms_percent must be a non-negative number")

    settings.default_pms_percent = pms_val
    db.commit()
    return {"default_pms_percent": settings.default_pms_percent}
