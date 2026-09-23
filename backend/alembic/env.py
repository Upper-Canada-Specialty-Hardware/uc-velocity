"""Alembic environment configuration for UC Velocity ERP.

This module configures Alembic to work with the application's SQLAlchemy models
and database connection.
"""
import os
from pathlib import Path
from logging.config import fileConfig

# Load environment variables from .env file (for local development)
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

from sqlalchemy import engine_from_config, pool
from alembic import context

# Import your models and Base - this populates Base.metadata
from database import Base
import models  # noqa: F401 - Import to register all models with Base.metadata

# Alembic Config object - provides access to alembic.ini values
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata from your models for autogenerate support
target_metadata = Base.metadata

# Tables that live in the database on purpose but have no model. Migration 023
# keeps a copy of the pre-bulk-markup values in ``_markup_backup_023`` so its
# downgrade can restore them; ``alembic check`` must not report it as drift.
_TABLES_WITHOUT_MODELS = {"_markup_backup_023"}


def include_object(obj, name, type_, reflected, compare_to):
    """Filter which schema objects autogenerate / ``alembic check`` compare.

    Args:
        obj: The SQLAlchemy schema object being considered.
        name: Its name.
        type_: "table", "column", "index", etc.
        reflected: True when the object came from the live database.
        compare_to: The counterpart object on the other side, or None.

    Returns:
        False for tables deliberately kept without a model; True otherwise.
    """
    if type_ == "table" and name in _TABLES_WITHOUT_MODELS:  # backup table from 023
        return False                                           # skip it -> not drift
    return True                                                # everything else compares normally


def get_url() -> str:
    """Get database URL from environment variable."""
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError(
            "DATABASE_URL environment variable is required.\n"
            "For local development, create a .env file with DATABASE_URL=postgresql://..."
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,  # ignore the 023 backup table
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine and associate a
    connection with the context.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,  # ignore the 023 backup table
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
