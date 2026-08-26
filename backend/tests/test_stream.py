"""Tests for the live feed."""

import json
from uuid import UUID

import pytest
from httpx import AsyncClient

from app.api import stream as stream_module
from app.core.redis import redis
from app.main import app
from app.services.bus import publish
from tests.sse import SSEStream


async def _endpoint(client: AsyncClient) -> dict[str, str]:
    return dict((await client.post("/api/endpoints", json={})).json())


async def _capture(client: AsyncClient, endpoint: dict[str, str], **kwargs: object) -> int:
    response = await client.post(f"/in/{endpoint['ingest_token']}", **kwargs)  # type: ignore[arg-type]
    request_id: int = response.json()["request_id"]
    return request_id


def _open(endpoint: dict[str, str], query: str = "", **headers: str) -> SSEStream:
    return SSEStream(
        app,
        f"/api/endpoints/{endpoint['view_token']}/stream",
        query=query,
        headers=headers,
    )


async def test_stream_announces_itself_before_any_traffic(client: AsyncClient) -> None:
    """A silent connection is indistinguishable from a broken one.

    The `ready` frame is what lets the UI say "listening" instead of showing an
    ambiguous blank while it waits for a webhook that may take minutes.
    """
    endpoint = await _endpoint(client)

    async with _open(endpoint) as sse:
        assert sse.status == 200
        assert sse.headers["content-type"].startswith("text/event-stream")
        assert sse.headers["cache-control"] == "no-cache"
        # Against a buffering proxy the feed arrives in bursts or not at all,
        # while the code itself is perfectly correct.
        assert sse.headers["x-accel-buffering"] == "no"

        frame = await sse.next_frame()

    assert frame["event"] == "ready"


async def test_a_capture_reaches_an_open_stream(client: AsyncClient) -> None:
    """The whole point of the phase: ingest and feed talk through Redis."""
    endpoint = await _endpoint(client)

    async with _open(endpoint) as sse:
        assert (await sse.next_frame())["event"] == "ready"

        request_id = await _capture(client, endpoint, json={"event": "ping"})
        frame = await sse.next_frame()

    assert frame["event"] == "request"
    # The SSE id is the capture id itself -- no cursor table, and it is what the
    # browser echoes back in Last-Event-ID on reconnect.
    assert frame["id"] == str(request_id)

    payload = json.loads(frame["data"])
    assert payload["id"] == request_id
    assert payload["method"] == "POST"


async def test_the_live_payload_carries_no_body(client: AsyncClient) -> None:
    """The feed renders a list, so it ships summaries.

    Streaming a megabyte per capture to draw a row of method, path and time would
    make a busy endpoint unusable. The body is fetched when a row is clicked.
    """
    endpoint = await _endpoint(client)

    async with _open(endpoint) as sse:
        await sse.next_frame()
        await _capture(client, endpoint, json={"payload": "x" * 500})
        payload = json.loads((await sse.next_frame())["data"])

    assert "body_text" not in payload
    assert "body_json" not in payload
    assert payload["body_size"] > 500


async def test_several_captures_arrive_in_order(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)

    async with _open(endpoint) as sse:
        await sse.next_frame()
        sent = [await _capture(client, endpoint, json={"n": n}) for n in range(5)]
        frames = await sse.next_frames(5)

    assert [int(frame["id"]) for frame in frames] == sent


async def test_a_stream_never_sees_another_endpoints_traffic(client: AsyncClient) -> None:
    """One stream key per endpoint is what keeps holders of one token from
    reading someone else's live traffic.
    """
    mine = await _endpoint(client)
    theirs = await _endpoint(client)

    async with _open(mine) as sse:
        await sse.next_frame()
        await _capture(client, theirs, json={"secret": "not yours"})
        await sse.expect_silence()


async def test_reconnecting_delivers_what_was_missed(client: AsyncClient) -> None:
    """The gap between disconnect and reconnect is refilled from Postgres.

    The stream is a bounded buffer; the database is the durable record. Asking
    it for "everything after N" is what makes a dropped connection a non-event.
    """
    endpoint = await _endpoint(client)
    first = await _capture(client, endpoint, json={"n": 1})
    second = await _capture(client, endpoint, json={"n": 2})
    third = await _capture(client, endpoint, json={"n": 3})

    async with _open(endpoint, query=f"last_event_id={first}") as sse:
        frames = await sse.next_frames(3)

    assert [frame["event"] for frame in frames] == ["request", "request", "ready"]
    assert [int(frame["id"]) for frame in frames[:2]] == [second, third]


async def test_the_browsers_own_header_is_honoured(client: AsyncClient) -> None:
    """`EventSource` resends Last-Event-ID by itself, with no application code.

    The query parameter exists for curl and for the future fetch-based client;
    the header is what a real browser actually sends.
    """
    endpoint = await _endpoint(client)
    first = await _capture(client, endpoint, json={"n": 1})
    second = await _capture(client, endpoint, json={"n": 2})

    async with _open(endpoint, **{"last-event-id": str(first)}) as sse:
        frame = await sse.next_frame()

    assert int(frame["id"]) == second


async def test_a_garbled_last_event_id_is_ignored(client: AsyncClient) -> None:
    """The header arrives from the network, so it can be anything at all.

    Treating it as absent degrades to "live only" instead of killing the
    connection with a 500.
    """
    endpoint = await _endpoint(client)
    await _capture(client, endpoint, json={"n": 1})

    async with _open(endpoint, **{"last-event-id": "not-a-number"}) as sse:
        frame = await sse.next_frame()

    assert frame["event"] == "ready"


async def test_backfill_and_live_feed_do_not_overlap(client: AsyncClient) -> None:
    """The race this phase is built around.

    The stream position is taken BEFORE reading Postgres, so nothing arriving
    during the backfill is lost. The price is that an event can appear in both,
    and the id filter is what stops it being delivered twice.
    """
    endpoint = await _endpoint(client)
    first = await _capture(client, endpoint, json={"n": 1})
    second = await _capture(client, endpoint, json={"n": 2})

    async with _open(endpoint, query=f"last_event_id={first}") as sse:
        # The backfill hands over #2, which is also still sitting in the stream.
        assert int((await sse.next_frame())["id"]) == second
        assert (await sse.next_frame())["event"] == "ready"

        third = await _capture(client, endpoint, json={"n": 3})
        frame = await sse.next_frame()

    assert int(frame["id"]) == third, "the live loop must not replay the backfilled capture"


async def test_a_client_too_far_behind_is_told_to_reload(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tab left open for a week must not trigger a dump of everything since.

    Announcing the gap is the honest answer: the client reloads through the REST
    API and resumes live. Silently trimming the backfill would leave a hole
    nobody could detect.
    """
    monkeypatch.setattr(stream_module, "MAX_BACKFILL", 2)

    endpoint = await _endpoint(client)
    for n in range(4):
        await _capture(client, endpoint, json={"n": n})

    async with _open(endpoint, query="last_event_id=0") as sse:
        frames = await sse.next_frames(2)

    assert frames[0]["event"] == "gap"
    assert json.loads(frames[0]["data"])["reason"] == "too_many_missed"
    assert frames[1]["event"] == "ready"


async def test_an_unknown_view_token_gets_no_stream(client: AsyncClient) -> None:
    response = await client.get("/api/endpoints/does-not-exist/stream")

    assert response.status_code == 404


async def test_the_ingest_token_cannot_open_the_stream(client: AsyncClient) -> None:
    """The public token is pasted into third-party panels and ends up in logs.

    If it also opened the feed, anyone who saw that URL could read the traffic --
    which routinely carries other people's credentials.
    """
    endpoint = await _endpoint(client)

    response = await client.get(f"/api/endpoints/{endpoint['ingest_token']}/stream")

    assert response.status_code == 404


async def test_out_of_order_arrivals_are_all_delivered(client: AsyncClient) -> None:
    """Stream order does not follow capture-id order, and the feed must not care.

    An id is handed out at flush; the announcement goes out after commit. Under
    concurrency those interleave, so capture 30 can reach the stream before 10.
    Filtering with a "highest id seen" watermark silently swallowed every arrival
    below the mark -- roughly half of them under real load, and never once in a
    sequential test.
    """
    endpoint = await _endpoint(client)

    async with _open(endpoint) as sse:
        assert (await sse.next_frame())["event"] == "ready"

        for request_id in (30, 20, 10):
            await publish(redis, UUID(endpoint["id"]), {"id": request_id, "method": "POST"})

        frames = await sse.next_frames(3)

    assert [int(frame["id"]) for frame in frames] == [30, 20, 10]
