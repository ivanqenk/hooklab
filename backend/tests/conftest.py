"""Fixtures shared across the test suite."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.core.redis import redis
from app.main import app


@pytest.fixture(autouse=True)
async def clean_state() -> AsyncIterator[None]:
    """Empty the tables and the live streams before every test.

    Without isolation tests contaminate each other: one leaves rows behind that
    another finds, producing failures that depend on execution order -- among the
    hardest to diagnose, because the test that fails is not the one with the bug.

    `RESTART IDENTITY` also resets the bigserial counter, so ids are deterministic
    on every run. `CASCADE` is required by the foreign key from requests to
    endpoints.
    """
    settings = get_settings()

    # Safety net: TRUNCATE deletes everything with no way back. If someone ever
    # pointed the tests at production through a misconfigured .env, this stops it
    # before the damage.
    if settings.is_production:
        raise RuntimeError("Tests must never run against production")

    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE signature_checks, requests, endpoints RESTART IDENTITY CASCADE")
        )

    # Only the endpoint streams, never FLUSHDB: the tests share this Redis with
    # local development. `KEYS` would be a bad idea against a real keyspace but is
    # exactly right against a handful of test keys.
    keys = await redis.keys("ep:*")
    if keys:
        await redis.delete(*keys)

    yield

    # pytest-asyncio builds a fresh event loop for every test, but the Redis
    # client is a module global and its pool holds sockets bound to the loop that
    # opened them. The next test then picks up a connection whose loop is closed
    # and dies with "Event loop is closed" -- during setup, so the failure points
    # at an innocent test.
    #
    # Disconnecting here, still inside the test's own loop, closes them cleanly.
    # The pool stays usable and opens new connections on demand. It also discards
    # any connection left mid-command by a cancelled blocking XREAD, which would
    # otherwise desynchronise the protocol for whoever got it next.
    await redis.connection_pool.disconnect()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client that talks to the application in-process.

    ASGITransport calls the app directly, without opening a port or starting a
    server. It is faster and sidesteps the "which port do the tests run on?"
    problem -- which already bit us once on this machine with 8000 taken.

    The dependencies (Postgres and Redis) are real: brought up with
    `docker compose up -d` locally, and as runner services in CI.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """Direct database session, for asserting on what was actually stored.

    The listing API does not exist yet, and even once it does, checking the rows
    themselves is the stronger assertion: it catches a field that is persisted
    wrongly but happens to be hidden by the serialisation layer.
    """
    async with SessionLocal() as session:
        yield session
