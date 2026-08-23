"""Entorno de migraciones de Alembic.

Dos cosas se conectan aquí con la aplicación real:

1. La URL de la base sale de nuestra configuración (el .env), NO de alembic.ini.
   Así hay una sola fuente de verdad y no se versiona ninguna credencial.
2. `target_metadata` apunta a `Base.metadata`, que es lo que Alembic compara
   contra la base real para autogenerar migraciones.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import get_settings

# Importar el paquete de modelos completo registra TODAS las tablas en
# Base.metadata. Si algún modelo no llega hasta aquí, Alembic no lo ve y genera
# migraciones vacías sin explicar por qué.
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()

# ConfigParser trata el signo % como sintaxis de interpolación, así que hay que
# escaparlo. Sin esto, una contraseña con % rompe las migraciones con un error
# que no menciona la contraseña por ninguna parte.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Genera el SQL sin conectarse a la base (`alembic upgrade --sql`)."""
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
        # Sin esto, Alembic ignora los cambios de tipo de una columna: cambiar un
        # String(50) a String(200) no generaría migración y lo descubrirías en
        # producción, al fallar un insert.
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
