"""Tests for creating and reading capture endpoints."""

from httpx import AsyncClient


async def test_create_returns_201_and_both_tokens(client: AsyncClient) -> None:
    response = await client.post("/api/endpoints", json={"name": "Stripe webhooks"})

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Stripe webhooks"
    assert body["ingest_token"]
    assert body["view_token"]


async def test_the_two_tokens_differ(client: AsyncClient) -> None:
    """The failure this guards against is subtle and would break the whole model.

    If the generator were evaluated once and both columns shared the result, the
    two tokens would be identical -- and then the public token, the one pasted
    into the provider's configuration, would also grant read access to all
    traffic. The two-token design would be silently defeated with nothing
    visibly broken.
    """
    body = (await client.post("/api/endpoints", json={})).json()

    assert body["ingest_token"] != body["view_token"]


async def test_each_endpoint_gets_its_own_tokens(client: AsyncClient) -> None:
    first = (await client.post("/api/endpoints", json={})).json()
    second = (await client.post("/api/endpoints", json={})).json()

    assert first["ingest_token"] != second["ingest_token"]
    assert first["view_token"] != second["view_token"]


async def test_ingest_url_contains_the_public_token(client: AsyncClient) -> None:
    body = (await client.post("/api/endpoints", json={})).json()

    assert body["ingest_url"].endswith(f"/in/{body['ingest_token']}")
    # And above all: the public URL must NOT carry the read token.
    assert body["view_token"] not in body["ingest_url"]


async def test_can_be_created_without_a_name(client: AsyncClient) -> None:
    response = await client.post("/api/endpoints", json={})

    assert response.status_code == 201
    assert response.json()["name"] is None


async def test_overly_long_name_is_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/endpoints", json={"name": "x" * 200})

    assert response.status_code == 422


async def test_reading_with_view_token_returns_the_endpoint(client: AsyncClient) -> None:
    created = (await client.post("/api/endpoints", json={"name": "my endpoint"})).json()

    response = await client.get(f"/api/endpoints/{created['view_token']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["name"] == "my endpoint"


async def test_reading_never_returns_the_view_token(client: AsyncClient) -> None:
    """The read token is handed out once, at creation time.

    Repeating it in every response would multiply the places it can leak from:
    browser history, proxy logs, screenshots.
    """
    created = (await client.post("/api/endpoints", json={})).json()

    response = await client.get(f"/api/endpoints/{created['view_token']}")

    assert "view_token" not in response.json()


async def test_ingest_token_does_not_grant_read_access(client: AsyncClient) -> None:
    """The central security test of this module.

    The ingest token is public by nature: it ends up in configuration panels,
    screenshots and third-party logs. If it also granted read access, anyone who
    saw it could read payloads containing credentials.
    """
    created = (await client.post("/api/endpoints", json={})).json()

    response = await client.get(f"/api/endpoints/{created['ingest_token']}")

    assert response.status_code == 404


async def test_unknown_token_returns_404(client: AsyncClient) -> None:
    response = await client.get("/api/endpoints/this-token-does-not-exist")

    assert response.status_code == 404
