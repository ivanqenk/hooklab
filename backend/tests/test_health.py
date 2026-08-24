"""Tests for the health endpoints.

The liveness/readiness distinction is a design decision, not a detail, so the
tests assert explicitly that the two behave DIFFERENTLY.
"""

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_does_not_check_dependencies(client: AsyncClient) -> None:
    """/health must not report anything about Postgres or Redis.

    If someone ever "improves" this endpoint by adding dependency checks, this
    test fails -- and it should. A liveness probe coupled to the database causes
    cascading restarts exactly when the system is already struggling.
    """
    response = await client.get("/health")

    assert "checks" not in response.json()


async def test_ready_reports_every_dependency(client: AsyncClient) -> None:
    """Requires Postgres and Redis to be up (docker compose up -d)."""
    response = await client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    # Assert the per-dependency diagnosis, not just a global boolean: knowing
    # WHICH one failed is half the value of the endpoint.
    assert body["checks"] == {"postgres": "ok", "redis": "ok"}
