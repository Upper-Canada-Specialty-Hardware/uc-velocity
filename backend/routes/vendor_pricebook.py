import csv
import io

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from database import get_db
from models import Profile, Part
from schemas import PricebookImportResult

router = APIRouter(prefix="/vendors", tags=["vendor_pricebook"])


@router.post("/{vendor_id}/pricebook/import", response_model=PricebookImportResult)
async def import_pricebook(
    vendor_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Import a CSV pricebook for a vendor.

    Required columns: part_number, list_price
    Optional columns: description, discount_percent

    For each row:
    - If a Part with that part_number exists: update list_price, discount_percent, vendor_id, recalculate cost
    - If not found: create new Part with the data
    """
    vendor = db.query(Profile).filter(Profile.id == vendor_id).first()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    if vendor.type.value != "vendor":
        raise HTTPException(status_code=400, detail="Profile is not a vendor")

    # Read and parse CSV
    try:
        content = await file.read()
        text = content.decode("utf-8-sig")  # Handle BOM
        reader = csv.DictReader(io.StringIO(text))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {str(e)}")

    # Validate required columns
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV file is empty or has no headers")

    fieldnames_lower = [f.strip().lower() for f in reader.fieldnames]
    if "part_number" not in fieldnames_lower or "list_price" not in fieldnames_lower:
        raise HTTPException(
            status_code=400,
            detail="CSV must have 'part_number' and 'list_price' columns"
        )

    # Build column index mapping (case-insensitive)
    col_map = {}
    for original in reader.fieldnames:
        col_map[original.strip().lower()] = original

    created = 0
    updated = 0
    errors = []

    for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
        part_number = row.get(col_map.get("part_number", ""), "").strip()
        list_price_str = row.get(col_map.get("list_price", ""), "").strip()

        if not part_number:
            errors.append(f"Row {row_num}: missing part_number")
            continue

        if not list_price_str:
            errors.append(f"Row {row_num}: missing list_price for {part_number}")
            continue

        try:
            list_price = float(list_price_str)
        except ValueError:
            errors.append(f"Row {row_num}: invalid list_price '{list_price_str}' for {part_number}")
            continue

        # Optional fields
        description = row.get(col_map.get("description", ""), "").strip() or None
        discount_str = row.get(col_map.get("discount_percent", ""), "").strip()
        discount_percent = None
        if discount_str:
            try:
                discount_percent = float(discount_str)
            except ValueError:
                errors.append(f"Row {row_num}: invalid discount_percent '{discount_str}' for {part_number}")
                continue

        # Find or create part
        existing = db.query(Part).filter(Part.part_number == part_number).first()

        if existing:
            existing.list_price = list_price
            existing.vendor_id = vendor_id
            if discount_percent is not None:
                existing.discount_percent = discount_percent
            if description:
                existing.description = description

            # Auto-calculate cost
            effective_discount = (
                existing.discount_percent
                if existing.discount_percent is not None
                else (vendor.default_discount_percent or 0)
            )
            existing.cost = list_price * (1 - effective_discount / 100)
            updated += 1
        else:
            effective_discount = (
                discount_percent
                if discount_percent is not None
                else (vendor.default_discount_percent or 0)
            )
            cost = list_price * (1 - effective_discount / 100)

            new_part = Part(
                part_number=part_number,
                description=description or part_number,
                list_price=list_price,
                cost=cost,
                vendor_id=vendor_id,
                discount_percent=discount_percent,
                markup_percent=50.0,
            )
            db.add(new_part)
            created += 1

    db.commit()

    return PricebookImportResult(
        created=created,
        updated=updated,
        errors=errors,
    )
