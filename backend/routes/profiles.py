from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from database import get_db
from models import Profile, ProfileType as ModelProfileType, Contact, ContactPhone, PhoneType as ModelPhoneType
from schemas import (
    Paginated,
    ProfileCreate, ProfileUpdate, Profile as ProfileSchema,
    ContactCreate, ContactUpdate, Contact as ContactSchema
)

router = APIRouter(prefix="/profiles", tags=["profiles"])

_CACHE_CONTROL = "private, max-age=60"
DEFAULT_LIMIT = 50
MAX_LIMIT = 10000


@router.get("/", response_model=Paginated[ProfileSchema])
def get_all_profiles(
    response: Response,
    offset: int = Query(0, ge=0),
    limit: int = Query(DEFAULT_LIMIT, ge=0, le=MAX_LIMIT),
    skip: int = Query(0, ge=0, deprecated=True, description="Deprecated alias for offset"),
    profile_type: Optional[str] = Query(None, description="Filter by profile type (customer or vendor)"),
    db: Session = Depends(get_db)
):
    """Paginated list of profiles with optional filtering by type.

    Pass `limit=0` to fetch every row (used by autocomplete loaders).
    """
    effective_offset = offset or skip
    base = db.query(Profile).options(
        joinedload(Profile.contacts).joinedload(Contact.phone_numbers)
    )
    if profile_type:
        base = base.filter(Profile.type == ModelProfileType(profile_type))
    total = base.with_entities(Profile.id).count()
    q = base.order_by(Profile.id).offset(effective_offset)
    if limit > 0:
        q = q.limit(limit)
    profiles = q.all()
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return Paginated[ProfileSchema](
        items=profiles, total=total, limit=limit, offset=effective_offset
    )


@router.get("/{profile_id}", response_model=ProfileSchema)
def get_profile(profile_id: int, response: Response, db: Session = Depends(get_db)):
    """Get a single profile by ID with nested contacts."""
    profile = db.query(Profile).options(
        joinedload(Profile.contacts).joinedload(Contact.phone_numbers)
    ).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    response.headers["Cache-Control"] = _CACHE_CONTROL
    return profile


@router.post("/", response_model=ProfileSchema)
def create_profile(profile_data: ProfileCreate, db: Session = Depends(get_db)):
    """Create a new profile with contacts."""
    # Create the profile
    db_profile = Profile(
        name=profile_data.name,
        type=ModelProfileType(profile_data.type.value),
        pst=profile_data.pst,
        address=profile_data.address,
        postal_code=profile_data.postal_code,
        default_discount_percent=getattr(profile_data, 'default_discount_percent', None)
    )
    db.add(db_profile)
    db.flush()  # Get the profile ID

    # Create contacts with phone numbers
    for contact_data in profile_data.contacts:
        db_contact = Contact(
            profile_id=db_profile.id,
            name=contact_data.name,
            job_title=contact_data.job_title,
            email=contact_data.email
        )
        db.add(db_contact)
        db.flush()  # Get the contact ID

        for phone_data in contact_data.phone_numbers:
            db_phone = ContactPhone(
                contact_id=db_contact.id,
                type=ModelPhoneType(phone_data.type.value),
                number=phone_data.number
            )
            db.add(db_phone)

    db.commit()
    db.refresh(db_profile)
    return db_profile


@router.put("/{profile_id}", response_model=ProfileSchema)
def update_profile(profile_id: int, profile_data: ProfileUpdate, db: Session = Depends(get_db)):
    """Update an existing profile (profile fields only, contacts managed separately)."""
    db_profile = db.query(Profile).options(
        joinedload(Profile.contacts).joinedload(Contact.phone_numbers)
    ).filter(Profile.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if profile_data.name is not None:
        db_profile.name = profile_data.name
    if profile_data.type is not None:
        db_profile.type = ModelProfileType(profile_data.type.value)
    if profile_data.pst is not None:
        db_profile.pst = profile_data.pst
    if profile_data.address is not None:
        db_profile.address = profile_data.address
    if profile_data.postal_code is not None:
        db_profile.postal_code = profile_data.postal_code
    if profile_data.default_discount_percent is not None:
        db_profile.default_discount_percent = profile_data.default_discount_percent

    db.commit()
    db.refresh(db_profile)
    return db_profile


@router.delete("/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Delete a profile (cascades to contacts)."""
    db_profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    # Check if profile is referenced by projects or POs
    if db_profile.projects:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete profile: referenced by existing projects"
        )
    if db_profile.purchase_orders:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete profile: referenced by existing purchase orders"
        )

    db.delete(db_profile)
    db.commit()
    return {"message": "Profile deleted successfully"}


# ===== Contact Management Endpoints =====

@router.post("/{profile_id}/contacts", response_model=ContactSchema)
def add_contact(profile_id: int, contact_data: ContactCreate, db: Session = Depends(get_db)):
    """Add a contact to a profile."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    db_contact = Contact(
        profile_id=profile_id,
        name=contact_data.name,
        job_title=contact_data.job_title,
        email=contact_data.email
    )
    db.add(db_contact)
    db.flush()

    for phone_data in contact_data.phone_numbers:
        db_phone = ContactPhone(
            contact_id=db_contact.id,
            type=ModelPhoneType(phone_data.type.value),
            number=phone_data.number
        )
        db.add(db_phone)

    db.commit()
    db.refresh(db_contact)
    return db_contact


@router.put("/{profile_id}/contacts/{contact_id}", response_model=ContactSchema)
def update_contact(
    profile_id: int,
    contact_id: int,
    contact_data: ContactUpdate,
    db: Session = Depends(get_db)
):
    """Update a contact (replaces phone numbers if provided)."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    db_contact = db.query(Contact).options(
        joinedload(Contact.phone_numbers)
    ).filter(
        Contact.id == contact_id,
        Contact.profile_id == profile_id
    ).first()
    if not db_contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    if contact_data.name is not None:
        db_contact.name = contact_data.name
    if contact_data.job_title is not None:
        db_contact.job_title = contact_data.job_title
    if contact_data.email is not None:
        db_contact.email = contact_data.email

    # If phone_numbers provided, replace all existing
    if contact_data.phone_numbers is not None:
        # Delete existing phone numbers
        db.query(ContactPhone).filter(ContactPhone.contact_id == contact_id).delete()

        # Add new phone numbers
        for phone_data in contact_data.phone_numbers:
            db_phone = ContactPhone(
                contact_id=contact_id,
                type=ModelPhoneType(phone_data.type.value),
                number=phone_data.number
            )
            db.add(db_phone)

    db.commit()
    db.refresh(db_contact)
    return db_contact


@router.delete("/{profile_id}/contacts/{contact_id}")
def delete_contact(profile_id: int, contact_id: int, db: Session = Depends(get_db)):
    """Delete a contact (cannot delete the last contact)."""
    profile = db.query(Profile).filter(Profile.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    contact = db.query(Contact).filter(
        Contact.id == contact_id,
        Contact.profile_id == profile_id
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    # Validate at least one contact remains
    contact_count = db.query(Contact).filter(Contact.profile_id == profile_id).count()
    if contact_count <= 1:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the last contact. Profile must have at least one contact."
        )

    db.delete(contact)
    db.commit()
    return {"message": "Contact deleted successfully"}
