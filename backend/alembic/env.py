"""Alembic migration environment.

Two things are wired to the real application here:

1. The database URL comes from our settings (the .env), NOT from alembic.ini.
   That keeps a single source of truth and keeps credentials out of version
   control.
2. `target_metadata` points at `Base.metadata`, which is what Alembic compares
   against the live database to autogenerate migrations.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings

# Importing the whole models package registers EVERY table on Base.metadata. Any
# model that never reaches this import is invisible to Alembic, which then emits
# empty migrations without explaining why.
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()

# ConfigParser treats % as interpolation syntax, so it has to be escaped. Without
# this, a password containing % breaks migrations with an error that never
# mentions the password.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without connecting to the database (`alembic upgrade --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without this Alembic ignores column type changes: turning a String(50)
        # into a String(200) would produce no migration, and you would find out
        # in production when an insert fails.
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
