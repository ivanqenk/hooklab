"""Tests for registering forwarding destinations."""

import pytest
from httpx import AsyncClient, Response

PUBLIC = "https://example.com/webhook"


async def _endpoint(client: AsyncClient) -> dict[str, str]:
    return dict((await client.post("/api/endpoints", json={})).json())


async def _create(client: AsyncClient, endpoint: dict[str, str], url: str = PUBLIC) -> Response:
    return await client.post(
        f"/api/endpoints/{endpoint['view_token']}/destinations", json={"target_url": url}
    )


async def test_a_destination_starts_unverified(client: AsyncClient) -> None:
    """Nothing is forwarded until ownership of the URL is proven.

    Created-and-immediately-forwarding would make Hooklab a free amplifier: point
    it at a stranger's site, send it traffic, and their server takes the load
    from our IP.
    """
    endpoint = await _endpoint(client)

    response = await _create(client, endpoint)

    assert response.status_code == 201
    body = response.json()
    assert body["verified"] is False
    assert body["target_url"] == PUBLIC


async def test_the_verification_token_is_returned(client: AsyncClient) -> None:
    """It is a challenge, not a credential.

    The user cannot configure their server to echo it back without being able to
    read it, and it grants nothing on its own.
    """
    endpoint = await _endpoint(client)

    body = (await _create(client, endpoint)).json()

    assert body["verification_token"]
    assert len(body["verification_token"]) >= 16


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hooks",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.1/hooks",
        "http://[::1]/hooks",
        "file:///etc/passwd",
        "http://2130706433/hooks",
    ],
)
async def test_dangerous_destinations_are_refused(client: AsyncClient, url: str) -> None:
    """Caught while the user is still looking at the screen.

    This is convenience, not the defence: DNS can change between now and the
    first delivery, so the worker validates again immediately before connecting.
    """
    endpoint = await _endpoint(client)

    response = await _create(client, endpoint, url)

    assert response.status_code == 422


async def test_destinations_are_listed_and_deleted(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)
    created = (await _create(client, endpoint)).json()

    listed = (await client.get(f"/api/endpoints/{endpoint['view_token']}/destinations")).json()
    assert [d["id"] for d in listed] == [created["id"]]

    deleted = await client.delete(
        f"/api/endpoints/{endpoint['view_token']}/destinations/{created['id']}"
    )
    assert deleted.status_code == 204

    assert (await client.get(f"/api/endpoints/{endpoint['view_token']}/destinations")).json() == []


async def test_one_endpoint_cannot_touch_anothers_destination(client: AsyncClient) -> None:
    """Same scoping rule as captures: the token bounds what it can reach."""
    mine = await _endpoint(client)
    theirs = await _endpoint(client)
    created = (await _create(client, theirs)).json()

    response = await client.delete(
        f"/api/endpoints/{mine['view_token']}/destinations/{created['id']}"
    )

    assert response.status_code == 404


async def test_the_ingest_token_cannot_register_a_destination(client: AsyncClient) -> None:
    """The public token must never be able to aim the forwarder."""
    endpoint = await _endpoint(client)

    response = await client.post(
        f"/api/endpoints/{endpoint['ingest_token']}/destinations", json={"target_url": PUBLIC}
    )

    assert response.status_code == 404


async def test_the_number_of_destinations_is_capped(client: AsyncClient) -> None:
    """An uncapped fan-out multiplies every capture into unbounded outbound traffic."""
    endpoint = await _endpoint(client)
    for n in range(10):
        assert (await _create(client, endpoint, f"https://example.com/hook-{n}")).status_code == 201

    response = await _create(client, endpoint, "https://example.com/one-too-many")

    assert response.status_code == 409
