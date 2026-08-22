"""Motor y sesiones de base de datos."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    # Manda un "SELECT 1" barato antes de entregar una conexión del pool. Sin esto,
    # cuando Postgres se reinicia, la primera petición que tome una conexión muerta
    # falla con un error confuso en lugar de reconectar sola.
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = async_sessionmaker(
    engine,
    # Sin esto, tras un commit SQLAlchemy marca los objetos como caducados y los
    # vuelve a consultar en cuanto tocas un atributo. En código asíncrono eso
    # dispara una consulta implícita fuera de contexto y truena. Es la trampa
    # número uno de SQLAlchemy en modo async.
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI: una sesión por petición, cerrada al terminar."""
    async with SessionLocal() as session:
        yield session
