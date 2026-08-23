"""Pruebas de los endpoints de salud.

La distinción entre liveness y readiness es una decisión de diseño, no un detalle:
por eso se prueba explícitamente que se comportan DISTINTO.
"""

from httpx import AsyncClient


async def test_health_responde_ok(client: AsyncClient) -> None:
    respuesta = await client.get("/health")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok"}


async def test_health_no_consulta_dependencias(client: AsyncClient) -> None:
    """/health no debe reportar nada sobre Postgres ni Redis.

    Si algún día alguien "mejora" este endpoint agregándole comprobaciones de
    dependencias, esta prueba falla — y debe fallar. Un liveness que depende de la
    base de datos provoca reinicios en cascada justo cuando el sistema ya sufre.
    """
    respuesta = await client.get("/health")

    assert "checks" not in respuesta.json()


async def test_ready_reporta_cada_dependencia(client: AsyncClient) -> None:
    """Requiere Postgres y Redis levantados (docker compose up -d)."""
    respuesta = await client.get("/ready")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["status"] == "ready"
    # Se comprueba el diagnóstico por dependencia, no solo un booleano global:
    # saber CUÁL falló es la mitad del valor del endpoint.
    assert cuerpo["checks"] == {"postgres": "ok", "redis": "ok"}
