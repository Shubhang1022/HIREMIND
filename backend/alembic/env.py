"""Alembic migration environment configuration.

Supports async SQLAlchemy (asyncpg) migrations.
All ORM models are imported via ``app.models`` so that Alembic can
auto-generate migration scripts from the current schema.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Ensure the backend/ directory is in sys.path so that `app.*` imports work ─
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Load .env file so DATABASE_URL is available ──────────────────────────────
from dotenv import load_dotenv
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
load_dotenv(_env_path, override=True)

# ── Import all models so their tables are registered on Base.metadata ────────
import app.models  # noqa: F401 — side-effects register models with Base.metadata
from app.core.database import Base  # noqa: E402

# ── Alembic Config ────────────────────────────────────────────────────────────
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The MetaData object from our declarative base.
target_metadata = Base.metadata

# Override sqlalchemy.url from the environment variable if set.
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    # configparser uses % for interpolation — escape any % chars in the URL
    config.set_main_option("sqlalchemy.url", _db_url.replace("%", "%%"))


# ── Offline migrations (no live DB connection) ────────────────────────────────
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine; the migration
    scripts are generated with literal SQL rather than executing against a DB.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ── Online migrations (live DB connection via asyncpg) ────────────────────────
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations within an async context."""
    # statement_cache_size=0 is required for Supabase pgBouncer pooler
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"statement_cache_size": 0},
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using the async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
