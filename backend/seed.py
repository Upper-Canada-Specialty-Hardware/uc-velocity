"""
Database seeding for system items and company settings.
Run this at application startup to ensure system items exist.
"""
from sqlalchemy.orm import Session
from sqlalchemy import inspect as sa_inspect
from models import Miscellaneous, CompanySettings, SystemRate

SYSTEM_MISC_ITEMS = [
    # Parking
    {
        "description": "Parking (1 Hour)",
        "unit_price": 20.0,
        "markup_percent": 50.0,
        "is_system_item": True
    },
    # Travel Distance items
    {
        "description": "Travel Distance (40km) (1 Day)",
        "unit_price": 160.0,
        "markup_percent": 50.0,
        "is_system_item": True
    },
    {
        "description": "Travel Distance (60km) (1 Day)",
        "unit_price": 295.0,
        "markup_percent": 88.0,
        "is_system_item": True
    },
    {
        "description": "Travel Distance (80km) (1 Day)",
        "unit_price": 365.0,
        "markup_percent": 111.0,
        "is_system_item": True
    },
    {
        "description": "Travel Distance (120km) (1 Day)",
        "unit_price": 565.0,
        "markup_percent": 170.0,
        "is_system_item": True
    },
    {
        "description": "Travel Distance (180km) (1 Day)",
        "unit_price": 750.0,
        "markup_percent": 225.0,
        "is_system_item": True
    },
    {
        "description": "Travel Distance (260km) (1 Day)",
        "unit_price": 745.0,
        "markup_percent": 224.0,
        "is_system_item": True
    },
    {
        "description": "Unlimited Travel Distance (1 Day)",
        "unit_price": 1320.0,
        "markup_percent": 396.0,
        "is_system_item": True
    },
]


DEFAULT_COMPANY_SETTINGS = {
    "name": "Upper Canada Specialty Hardware",
    "address": "7100 Warden Ave, Unit #1, Markham, Ontario, L3R 8B5",
    "phone": "(905) 948-8350",
    "fax": "(905) 948-8392",
    "gst_number": "",
    "hst_rate": 13.0,
}


def seed_system_items(db: Session) -> None:
    """
    Seed system miscellaneous items and company settings if they don't exist.
    Called at application startup.
    """
    # Fast path: if the count matches what we expect to seed, nothing to do.
    # Avoids N filter().first() queries on every cold start (× every worker).
    expected_count = len(SYSTEM_MISC_ITEMS)
    actual_count = db.query(Miscellaneous).filter(
        Miscellaneous.is_system_item == True
    ).count()

    if actual_count < expected_count:
        # Some (or all) system items are missing — fetch existing descriptions
        # in a single query, then only insert what's not already there.
        existing_descriptions = {
            row[0] for row in db.query(Miscellaneous.description).filter(
                Miscellaneous.is_system_item == True
            ).all()
        }
        for item_data in SYSTEM_MISC_ITEMS:
            if item_data["description"] not in existing_descriptions:
                db.add(Miscellaneous(**item_data))

    # Seed company settings (singleton)
    existing_settings = db.query(CompanySettings).first()
    if not existing_settings:
        db.add(CompanySettings(**DEFAULT_COMPANY_SETTINGS))

    db.commit()

    # Seed default_pms_percent on company_settings if NULL
    settings = db.query(CompanySettings).first()
    if settings and settings.default_pms_percent is None:
        settings.default_pms_percent = 10.0
        db.commit()

    # Seed system_rates from misc items (if table exists and is empty)
    inspector = sa_inspect(db.bind)
    if 'system_rates' in inspector.get_table_names():
        existing_rates = db.query(SystemRate).count()
        if existing_rates == 0:
            # Map descriptions to (rate_type, sort_order)
            rate_map = {
                "Parking (1 Hour)": ("parking", 0),
                "Travel Distance (40km) (1 Day)": ("travel_distance", 1),
                "Travel Distance (60km) (1 Day)": ("travel_distance", 2),
                "Travel Distance (80km) (1 Day)": ("travel_distance", 3),
                "Travel Distance (120km) (1 Day)": ("travel_distance", 4),
                "Travel Distance (180km) (1 Day)": ("travel_distance", 5),
                "Travel Distance (260km) (1 Day)": ("travel_distance", 6),
                "Unlimited Travel Distance (1 Day)": ("travel_distance", 7),
            }

            misc_items = db.query(Miscellaneous).filter(
                Miscellaneous.is_system_item == True
            ).all()

            for misc in misc_items:
                mapping = rate_map.get(misc.description)
                if mapping:
                    rate_type, sort_order = mapping
                    db.add(SystemRate(
                        rate_type=rate_type,
                        description=misc.description,
                        unit_price=misc.unit_price,
                        markup_percent=misc.markup_percent,
                        sort_order=sort_order,
                        is_active=True,
                        linked_misc_id=misc.id,
                    ))

            db.commit()
