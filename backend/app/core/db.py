"""Database engine and sessions."""

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
    # Sends a cheap "SELECT 1" before handing out a pooled connection. Without it,
    # after Postgres restarts, the first request that picks up a dead connection
    # fails with a confusing error instead of reconnecting on its own.
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = async_sessionmaker(
    engine,
    # Without this, SQLAlchemy expires objects after each commit and re-queries
    # them as soon as an attribute is touched. In async code that fires an
    # implicit query outside of any context and blows up. It is the number one
    # trap of SQLAlchemy in async mode.
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, closed on the way out."""
    async with SessionLocal() as session:
        yield session
