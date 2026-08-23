"""Fixtures compartidas por las pruebas."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """Cliente HTTP que habla con la aplicación en memoria.

    ASGITransport llama a la app directamente, sin abrir un puerto ni levantar un
    servidor. Es más rápido y evita el problema de "¿en qué puerto corro las
    pruebas?" — que en esta máquina ya nos mordió una vez con el 8000 ocupado.

    Las dependencias (Postgres y Redis) sí son reales: se levantan con
    `docker compose up -d` en local, y como servicios del runner en CI.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
