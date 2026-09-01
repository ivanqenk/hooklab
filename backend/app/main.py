"""Hooklab application entry point."""

import logging

from fastapi import FastAPI, Response
from sqlalchemy import text

from app.api import destinations, endpoints, ingest, requests, stream
from app.core.config import get_settings
from app.core.db import engine
from app.core.redis import redis

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(title="Hooklab", version="0.1.0")

app.include_router(endpoints.router)
app.include_router(requests.router)
app.include_router(destinations.router)
app.include_router(stream.router)
app.include_router(ingest.router)


@app.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    """Liveness: is the process still alive?

    It does NOT check dependencies, and that is deliberate. If Postgres goes down
    this process is still healthy: restarting it would not fix the database, it
    would only drop the connections that do work. A liveness probe that checks
    dependencies causes cascading restarts exactly when the system is already
    struggling.
    """
    return {"status": "ok"}


@app.get("/ready", tags=["infra"])
async def ready(response: Response) -> dict[str, object]:
    """Readiness: can this instance serve traffic right now?

    Here dependencies ARE checked. Returns 503 when any of them fails so a load
    balancer stops sending requests to this instance without killing it: once the
    dependency recovers, the instance rejoins on its own.
    """
    checks: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure, never swallow it
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"

    all_ok = all(value == "ok" for value in checks.values())
    response.status_code = 200 if all_ok else 503
    return {"status": "ready" if all_ok else "degraded", "checks": checks}
