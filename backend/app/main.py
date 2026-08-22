"""Punto de entrada de la aplicación Hooklab."""

import logging

from fastapi import FastAPI, Response
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import engine
from app.core.redis import redis

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(title="Hooklab", version="0.1.0")


@app.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    """Liveness: ¿el proceso sigue vivo?

    NO consulta dependencias, y eso es deliberado. Si Postgres se cae, este proceso
    sigue sano: reiniciarlo no arreglaría la base de datos, solo tiraría las
    conexiones que sí funcionan. Un liveness que comprueba dependencias provoca
    reinicios en cascada justo cuando el sistema ya está sufriendo.
    """
    return {"status": "ok"}


@app.get("/ready", tags=["infra"])
async def ready(response: Response) -> dict[str, object]:
    """Readiness: ¿puedo atender tráfico en este momento?

    Aquí SÍ se consultan las dependencias. Devuelve 503 cuando alguna falla, para
    que el balanceador deje de mandarle peticiones a esta instancia sin matarla:
    en cuanto la dependencia vuelva, la instancia se reincorpora sola.
    """
    checks: dict[str, str] = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - queremos reportar cualquier fallo, no filtrarlo
        checks["postgres"] = f"error: {type(exc).__name__}"

    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"

    todo_ok = all(v == "ok" for v in checks.values())
    response.status_code = 200 if todo_ok else 503
    return {"status": "ready" if todo_ok else "degraded", "checks": checks}
