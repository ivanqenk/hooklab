"""Tests for reading captured requests."""

from httpx import AsyncClient


async def _endpoint(client: AsyncClient) -> dict[str, str]:
    return dict((await client.post("/api/endpoints", json={})).json())


async def _capture(client: AsyncClient, endpoint: dict[str, str], **kwargs: object) -> None:
    await client.post(f"/in/{endpoint['ingest_token']}", **kwargs)  # type: ignore[arg-type]


async def test_empty_endpoint_lists_nothing(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)

    response = await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}


async def test_lists_newest_first(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)
    for n in range(3):
        await _capture(client, endpoint, json={"n": n})

    body = (await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")).json()

    ids = [item["id"] for item in body["items"]]
    assert ids == sorted(ids, reverse=True)
    assert len(ids) == 3


async def test_summary_carries_no_body(client: AsyncClient) -> None:
    """A listing must stay small: fifty captures of a megabyte each is not a table."""
    endpoint = await _endpoint(client)
    await _capture(client, endpoint, json={"payload": "x" * 500})

    body = (await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")).json()

    item = body["items"][0]
    assert "body_text" not in item
    assert "body_json" not in item
    assert item["body_size"] > 500


async def test_detail_returns_everything_that_arrived(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)
    await client.post(
        f"/in/{endpoint['ingest_token']}/hooks?source=stripe",
        content=b'{"event":"ping"}',
        headers={"content-type": "application/json", "x-custom": "value"},
    )
    listed = (await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")).json()
    request_id = listed["items"][0]["id"]

    detail = (
        await client.get(f"/api/endpoints/{endpoint['view_token']}/requests/{request_id}")
    ).json()

    assert detail["path"] == "/hooks"
    assert detail["query"] == {"source": "stripe"}
    assert detail["headers"]["x-custom"] == "value"
    assert detail["body_json"] == {"event": "ping"}
    assert detail["body_text"] == '{"event":"ping"}'
    assert detail["body_encoding"] == "utf-8"
    assert detail["body_base64"] is None


async def test_binary_body_comes_back_base64(client: AsyncClient) -> None:
    """JSON cannot carry arbitrary bytes, so undecodable bodies switch to base64."""
    endpoint = await _endpoint(client)
    await client.post(
        f"/in/{endpoint['ingest_token']}",
        content=b"\xff\xfe\x00\x01binary",
        headers={"content-type": "application/octet-stream"},
    )
    listed = (await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")).json()
    request_id = listed["items"][0]["id"]

    detail = (
        await client.get(f"/api/endpoints/{endpoint['view_token']}/requests/{request_id}")
    ).json()

    assert detail["body_encoding"] == "base64"
    assert detail["body_text"] is None
    assert detail["body_base64"]


async def test_cursor_pagination_walks_the_whole_set(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)
    for n in range(5):
        await _capture(client, endpoint, json={"n": n})

    seen: list[int] = []
    cursor: int | None = None
    for _ in range(5):  # bounded, so a broken cursor cannot loop forever
        url = f"/api/endpoints/{endpoint['view_token']}/requests?limit=2"
        if cursor is not None:
            url += f"&before={cursor}"
        page = (await client.get(url)).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == 5
    assert len(set(seen)) == 5, "cursor pagination must not repeat rows"


async def test_last_page_has_no_cursor(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)
    await _capture(client, endpoint, json={})

    page = (await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")).json()

    assert page["next_cursor"] is None


async def test_limit_above_the_maximum_is_rejected(client: AsyncClient) -> None:
    """An unbounded limit is a denial-of-service invitation."""
    endpoint = await _endpoint(client)

    response = await client.get(f"/api/endpoints/{endpoint['view_token']}/requests?limit=5000")

    assert response.status_code == 422


async def test_cannot_read_another_endpoints_capture(client: AsyncClient) -> None:
    """The security test that matters most in this module.

    Capture ids are consecutive integers, so guessing them is trivial. Scoping
    every lookup by endpoint_id is what stops one token from reading another
    endpoint's traffic.
    """
    mine = await _endpoint(client)
    theirs = await _endpoint(client)
    await _capture(client, theirs, json={"secret": "not yours"})

    listed = (await client.get(f"/api/endpoints/{theirs['view_token']}/requests")).json()
    their_request_id = listed["items"][0]["id"]

    response = await client.get(f"/api/endpoints/{mine['view_token']}/requests/{their_request_id}")

    assert response.status_code == 404


async def test_unknown_request_id_returns_404(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)

    response = await client.get(f"/api/endpoints/{endpoint['view_token']}/requests/999999")

    assert response.status_code == 404


async def test_ingest_token_cannot_list_requests(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)
    await _capture(client, endpoint, json={})

    response = await client.get(f"/api/endpoints/{endpoint['ingest_token']}/requests")

    assert response.status_code == 404


async def test_body_download_is_never_served_as_its_own_type(client: AsyncClient) -> None:
    """Echoing back an attacker-chosen content type turns the domain into a
    malware host. Always octet-stream, always an attachment, always nosniff.
    """
    endpoint = await _endpoint(client)
    await client.post(
        f"/in/{endpoint['ingest_token']}",
        content=b"<script>alert(1)</script>",
        headers={"content-type": "text/html"},
    )
    listed = (await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")).json()
    request_id = listed["items"][0]["id"]

    response = await client.get(
        f"/api/endpoints/{endpoint['view_token']}/requests/{request_id}/body"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/octet-stream"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"
    # And the bytes themselves come back untouched.
    assert response.content == b"<script>alert(1)</script>"
