"""Alembic environment.

The URL resolves in one order, everywhere: the value set on the Alembic config
(tests and the gate point at a scratch database), then Settings. The .ini ships
no URL, so no machine path is committed.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from repcut.config import get_settings
from repcut.db import models as models  # registers every table on Base.metadata
from repcut.db.base import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False so a migration run cannot silence the
    # engine's structlog configuration in a shared process.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    """Resolve the target database, preferring an explicitly configured URL."""
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return get_settings().resolved_database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # render_as_batch: SQLite cannot ALTER a column or drop a constraint, so
    # Alembic has to copy the table. Without this every later schema change
    # fails on the only database this project ships.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against the async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations with a live connection."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
