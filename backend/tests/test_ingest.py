"""Tests for webhook ingestion."""

from collections.abc import Callable, Iterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.main import app
from app.models import CapturedRequest


async def _new_endpoint(client: AsyncClient) -> dict[str, str]:
    return dict((await client.post("/api/endpoints", json={})).json())


async def _stored(db: AsyncSession) -> CapturedRequest:
    """The single captured request, failing loudly if there is not exactly one."""
    result = await db.execute(select(CapturedRequest))
    return result.scalars().one()


@pytest.fixture
def with_body_limit() -> Callable[[int], None]:
    """Override the body limit so truncation can be tested without moving a megabyte."""

    def apply(limit: int) -> None:
        base = get_settings()

        def override() -> Settings:
            return base.model_copy(update={"max_body_bytes": limit})

        app.dependency_overrides[get_settings] = override

    return apply


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


async def test_captures_a_json_post(client: AsyncClient, db: AsyncSession) -> None:
    endpoint = await _new_endpoint(client)

    response = await client.post(
        f"/in/{endpoint['ingest_token']}",
        json={"event": "payment.succeeded", "amount": 4200},
    )

    assert response.status_code == 200
    assert response.json()["received"] is True

    stored = await _stored(db)
    assert stored.method == "POST"
    assert stored.body_json == {"event": "payment.succeeded", "amount": 4200}
    assert stored.body_truncated is False


async def test_raw_body_is_preserved_byte_for_byte(client: AsyncClient, db: AsyncSession) -> None:
    """The raw bytes are the source of truth: signature HMACs are computed on them.

    A body that is valid JSON but formatted unusually must come back exactly as
    sent, not re-serialised into a canonical form.
    """
    endpoint = await _new_endpoint(client)
    payload = b'{"a":  1,   "b":2}'

    await client.post(
        f"/in/{endpoint['ingest_token']}",
        content=payload,
        headers={"content-type": "application/json"},
    )

    stored = await _stored(db)
    assert stored.body_raw == payload
    assert stored.body_json == {"a": 1, "b": 2}


async def test_non_json_body_is_stored_without_parsing(
    client: AsyncClient, db: AsyncSession
) -> None:
    endpoint = await _new_endpoint(client)

    await client.post(
        f"/in/{endpoint['ingest_token']}",
        content=b"<event><type>ping</type></event>",
        headers={"content-type": "application/xml"},
    )

    stored = await _stored(db)
    assert stored.body_raw == b"<event><type>ping</type></event>"
    assert stored.body_json is None
    assert stored.content_type == "application/xml"


async def test_malformed_json_is_kept_raw(client: AsyncClient, db: AsyncSession) -> None:
    """A provider sending broken JSON is exactly what the developer needs to see."""
    endpoint = await _new_endpoint(client)

    await client.post(
        f"/in/{endpoint['ingest_token']}",
        content=b'{"broken": ',
        headers={"content-type": "application/json"},
    )

    stored = await _stored(db)
    assert stored.body_raw == b'{"broken": '
    assert stored.body_json is None


@pytest.mark.parametrize("method", ["GET", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def test_accepts_every_method(client: AsyncClient, db: AsyncSession, method: str) -> None:
    endpoint = await _new_endpoint(client)

    response = await client.request(method, f"/in/{endpoint['ingest_token']}")

    assert response.status_code == 200
    stored = await _stored(db)
    assert stored.method == method


async def test_captures_query_parameters(client: AsyncClient, db: AsyncSession) -> None:
    endpoint = await _new_endpoint(client)

    await client.get(f"/in/{endpoint['ingest_token']}?source=stripe&attempt=2")

    stored = await _stored(db)
    assert stored.query == {"source": "stripe", "attempt": "2"}


async def test_captures_headers(client: AsyncClient, db: AsyncSession) -> None:
    endpoint = await _new_endpoint(client)

    await client.post(
        f"/in/{endpoint['ingest_token']}",
        content=b"{}",
        headers={"content-type": "application/json", "x-hub-signature-256": "sha256=abc"},
    )

    stored = await _stored(db)
    # Header names arrive lowercased from the ASGI layer.
    assert stored.headers["x-hub-signature-256"] == "sha256=abc"


async def test_captures_sub_paths(client: AsyncClient, db: AsyncSession) -> None:
    """Providers append their own segments, and developers use paths to sort events."""
    endpoint = await _new_endpoint(client)

    response = await client.post(f"/in/{endpoint['ingest_token']}/stripe/charges")

    assert response.status_code == 200
    stored = await _stored(db)
    assert stored.path == "/stripe/charges"


async def test_bare_url_records_an_empty_path(client: AsyncClient, db: AsyncSession) -> None:
    endpoint = await _new_endpoint(client)

    await client.post(f"/in/{endpoint['ingest_token']}")

    stored = await _stored(db)
    assert stored.path == ""


async def test_oversized_body_is_truncated_and_flagged(
    client: AsyncClient, db: AsyncSession, with_body_limit: Callable[[int], None]
) -> None:
    """The limit exists so a huge body cannot exhaust the process.

    Storing a truncated copy rather than rejecting is deliberate: seeing the
    first bytes of what arrived is far more useful to a developer than nothing.
    """
    endpoint = await _new_endpoint(client)
    with_body_limit(100)

    response = await client.post(f"/in/{endpoint['ingest_token']}", content=b"x" * 5000)

    assert response.status_code == 200
    stored = await _stored(db)
    assert stored.body_truncated is True
    assert stored.body_size == 100
    assert stored.body_raw == b"x" * 100


async def test_body_exactly_at_the_limit_is_not_flagged(
    client: AsyncClient, db: AsyncSession, with_body_limit: Callable[[int], None]
) -> None:
    """Off-by-one guard: exactly the limit fits, it is not truncation."""
    endpoint = await _new_endpoint(client)
    with_body_limit(100)

    await client.post(f"/in/{endpoint['ingest_token']}", content=b"y" * 100)

    stored = await _stored(db)
    assert stored.body_truncated is False
    assert stored.body_size == 100


async def test_unknown_token_returns_404(client: AsyncClient) -> None:
    response = await client.post("/in/this-token-does-not-exist", json={})

    assert response.status_code == 404


async def test_request_count_increments(client: AsyncClient) -> None:
    endpoint = await _new_endpoint(client)

    for _ in range(3):
        await client.post(f"/in/{endpoint['ingest_token']}", json={})

    view = await client.get(f"/api/endpoints/{endpoint['view_token']}")
    assert view.json()["request_count"] == 3


async def test_view_token_does_not_work_for_ingest(client: AsyncClient) -> None:
    """The read token must not double as an ingest token.

    They are separate capabilities on purpose; accepting either one at either
    place would quietly collapse the distinction.
    """
    endpoint = await _new_endpoint(client)

    response = await client.post(f"/in/{endpoint['view_token']}", json={})

    assert response.status_code == 404
