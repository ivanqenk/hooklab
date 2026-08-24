"""Fixtures shared across the test suite."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine
from app.main import app


@pytest.fixture(autouse=True)
async def clean_database() -> AsyncIterator[None]:
    """Empty the tables before every test.

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
        await conn.execute(text("TRUNCATE requests, endpoints RESTART IDENTITY CASCADE"))

    yield


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
