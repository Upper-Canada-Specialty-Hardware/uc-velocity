import os
from pathlib import Path

# Load environment variables from .env file (for local development)
from dotenv import load_dotenv
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import engine, Base, SessionLocal
from routes import parts, labor, profiles, projects, quotes, purchase_orders, miscellaneous, invoices, company_settings, reports, cost_codes, vendor_pricebook, migration, system_rates, testing
from seed import seed_system_items
from auth import ActorMiddleware


def run_migrations():
    """Run Alembic migrations on startup, gated by ALEMBIC_AUTO_UPGRADE=1.

    Gating rationale: with multiple Gunicorn workers, this runs once per
    worker on cold start — which loads alembic, inspects the schema, and
    optionally runs `upgrade head`. For RAM and cold-start cost, we skip
    all of that unless the operator opts in by setting ALEMBIC_AUTO_UPGRADE=1.
    Schema changes should be triggered explicitly (set the env var for the
    schema-change deploy, or run `railway run alembic upgrade head` manually).

    Legacy-stamp self-healing is preserved when the gate is open: if app
    tables exist but alembic_version doesn't, we stamp instead of upgrading.
    """
    if os.getenv("ALEMBIC_AUTO_UPGRADE") != "1":
        print("[STARTUP] Skipping migrations (set ALEMBIC_AUTO_UPGRADE=1 to enable)")
        return

    from sqlalchemy import inspect
    from alembic.config import Config
    from alembic import command

    alembic_cfg = Config(os.path.join(os.path.dirname(__file__), "alembic.ini"))

    inspector = inspect(engine)
    tables = inspector.get_table_names()
    has_app_tables = "quotes" in tables or "profiles" in tables
    has_alembic = "alembic_version" in tables

    if has_app_tables and not has_alembic:
        command.stamp(alembic_cfg, "head")
        print("[STARTUP] Legacy database detected — stamped alembic_version to head")
    else:
        command.upgrade(alembic_cfg, "head")
        print("[STARTUP] Alembic migrations applied")


# 1. Run Alembic migrations (the single source of truth for schema)
try:
    run_migrations()
except Exception as e:
    print(f"[STARTUP] Migration error: {e}")
    # Fallback for truly fresh databases with no tables at all
    Base.metadata.create_all(bind=engine)
    print("[STARTUP] Fallback: created tables via create_all()")

# 2. Seed system items
def init_db():
    db = SessionLocal()
    try:
        seed_system_items(db)
    finally:
        db.close()

init_db()

app = FastAPI(
    title="UC Velocity ERP",
    description="Enterprise Resource Planning System for managing Customers, Vendors, Inventory, and Projects",
    version="1.0.0"
)

# Get CORS origins from environment variable, with sensible defaults for local dev
default_origins = [
    "http://localhost:5173",  # Vite dev server
    "http://localhost:3000",  # Alternative dev port
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env:
    # Add production origins from environment variable
    additional_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]
    allowed_origins = default_origins + additional_origins
else:
    allowed_origins = default_origins

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Capture the acting Clerk user (best-effort, never blocks) for the audit trail
app.add_middleware(ActorMiddleware)

# Include routers
app.include_router(parts.router)
app.include_router(labor.router)
app.include_router(profiles.router)
app.include_router(projects.router)
app.include_router(quotes.router)
app.include_router(purchase_orders.router)
app.include_router(miscellaneous.router)
app.include_router(invoices.router)
app.include_router(company_settings.router)
app.include_router(reports.router)
app.include_router(cost_codes.router)
app.include_router(vendor_pricebook.router)
app.include_router(migration.router)
app.include_router(system_rates.router)
app.include_router(testing.router)


@app.get("/")
def root():
    """Root endpoint to verify API is running."""
    return {
        "message": "UC Velocity ERP API",
        "status": "running",
        "version": "1.0.0"
    }


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
