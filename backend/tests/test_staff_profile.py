"""Tests for the staff-import UI backend: staff_roles persists on create/update, and
a staff profile can be created without an address/postal code (Vision staff have none).

DB-dependent (skipped via conftest when Postgres is unreachable, like the other DB tests).
"""
import pytest
from fastapi.testclient import TestClient

import main
from database import SessionLocal
from models import Profile

client = TestClient(main.app)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _cleanup(db, profile_id):
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if p is not None:
        db.delete(p)
        db.commit()


def test_create_staff_persists_roles_and_allows_no_address(db):
    """A staff profile with roles and no address/postal saves, and staff_roles round-trips."""
    r = client.post("/profiles/", json={
        "name": "[TEST] Jane Installer",
        "type": "staff",
        "staff_roles": "Lead, Installer",
        "contacts": [{"name": "[TEST] Jane Installer", "phone_numbers": []}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["staff_roles"] == "Lead, Installer"
    assert body["address"] in (None, "")           # no address required for staff

    db.expire_all()
    row = db.query(Profile).filter(Profile.id == body["id"]).first()
    assert row.staff_roles == "Lead, Installer"     # actually persisted, not dropped
    _cleanup(db, body["id"])


def test_update_staff_roles_persists(db):
    """Editing a staff member's roles through the update endpoint saves the new value."""
    created = client.post("/profiles/", json={
        "name": "[TEST] Bob Lead",
        "type": "staff",
        "staff_roles": "Lead",
        "contacts": [{"name": "[TEST] Bob Lead", "phone_numbers": []}],
    }).json()

    r = client.put(f"/profiles/{created['id']}", json={"staff_roles": "Lead, Manager"})
    assert r.status_code == 200, r.text
    assert r.json()["staff_roles"] == "Lead, Manager"

    db.expire_all()
    row = db.query(Profile).filter(Profile.id == created["id"]).first()
    assert row.staff_roles == "Lead, Manager"
    _cleanup(db, created["id"])
