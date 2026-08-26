"""Live capture feed over Server-Sent Events."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from redis.asyncio import Redis
from sqlalchemy import select
from starlette.responses import StreamingResponse

from app.api.deps import DetachedEndpointDep, RedisDep
from app.core.db import SessionLocal
from app.models import CapturedRequest
from app.schemas.request import RequestSummary
from app.services.bus import current_position, read

router = APIRouter(prefix="/api/endpoints/{view_token}", tags=["stream"])

# How long `XREAD` waits before giving up and letting the loop send a heartbeat.
# Comfortably under the 30-60s idle timeout of a typical proxy, which would
# otherwise cut a quiet connection and force a pointless reconnect.
BLOCK_MS = 25_000

# Ceiling on a single reconnect's backfill. A client that has been away for a
# week must not trigger a dump of every capture since. Past this, it is told to
# reload the list instead.
MAX_BACKFILL = 500


def _sse(data: dict[str, Any], event: str, event_id: int | None = None) -> str:
    """Format one SSE frame.

    The blank line at the end is what marks the frame complete; without it the
    browser buffers indefinitely and nothing is ever dispatched.
    """
    lines = []
    if event_id is not None:
        # What the browser echoes back in `Last-Event-ID` when it reconnects.
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"


def _requested_cursor(request: Request, last_event_id: int | None) -> int | None:
    """Where the client says it left off.

    `EventSource` reconnects on its own and resends the last id it saw in the
    `Last-Event-ID` header, with no application code involved. The query
    parameter is the manual equivalent, for `curl` and for the day the frontend
    moves to `fetch` streaming (which it must, once the token travels in an
    Authorization header, because `EventSource` cannot send custom headers).

    The header comes from the network, so a non-numeric value is treated as
    absent rather than crashing the connection.
    """
    if last_event_id is not None:
        return last_event_id

    raw = request.headers.get("last-event-id")
    if raw is None:
        return None

    try:
        return int(raw)
    except ValueError:
        return None


async def _backfill(endpoint_id: UUID, after_id: int) -> tuple[list[dict[str, Any]], bool]:
    """Captures newer than `after_id`, read from the durable record.

    Deliberately Postgres and not the stream: the stream is a bounded buffer and
    the entries being asked for may well have been trimmed off it already.

    The session is opened and closed right here rather than being injected. A
    yield dependency stays open until the response ends, and an SSE response ends
    when the user closes the tab -- hours later -- so injecting one would pin a
    pooled database connection for the whole time.
    """
    async with SessionLocal() as session:
        rows = list(
            (
                await session.execute(
                    select(CapturedRequest)
                    .where(
                        CapturedRequest.endpoint_id == endpoint_id,
                        CapturedRequest.id > after_id,
                    )
                    .order_by(CapturedRequest.id.asc())
                    .limit(MAX_BACKFILL + 1)
                )
            )
            .scalars()
            .all()
        )

    too_far_behind = len(rows) > MAX_BACKFILL
    if too_far_behind:
        return [], True

    return [RequestSummary.model_validate(row).model_dump(mode="json") for row in rows], False


async def _events(redis: Redis, endpoint_id: UUID, after_id: int | None) -> AsyncIterator[str]:
    """The frames of one connection, in order."""
    # Taken BEFORE the backfill, and that order is the whole trick. Anything that
    # arrives while Postgres is being read lands after this mark, so `XREAD` still
    # delivers it. Backfilling first and subscribing second leaves a window whose
    # events are lost with nothing to detect it. The cost is the odd duplicate,
    # which `delivered` filters out -- a far better failure than a silent gap.
    cursor = await current_position(redis, endpoint_id)

    # The ids the backfill already handed over, so the live loop can recognise the
    # overlap. It has to be the actual set and NOT a "highest id seen" watermark:
    # stream order does not follow id order. An id is assigned at flush and
    # published after commit, and under concurrency those interleave, so capture 87
    # routinely reaches the stream ahead of 85. A watermark would raise itself to
    # 87 and discard 85 for good -- a silent, load-dependent hole.
    #
    # Bounded by MAX_BACKFILL, and it never grows afterwards: the stream cursor only
    # moves forward, so a live entry is never read twice.
    delivered: set[int] = set()

    if after_id is not None:
        missed, too_far_behind = await _backfill(endpoint_id, after_id)
        if too_far_behind:
            # Honest about the limit instead of quietly skipping rows: the client
            # reloads the list through the REST API and carries on from live.
            yield _sse({"reason": "too_many_missed"}, event="gap")
        for payload in missed:
            delivered.add(payload["id"])
            yield _sse(payload, event="request", event_id=payload["id"])

    # Sent after the backfill, so it means "you are caught up, everything from
    # here is live" and not merely "socket open". It also gives a silent endpoint
    # something to show: without it, a working connection with no traffic looks
    # exactly like a broken one.
    yield _sse({"endpoint_id": str(endpoint_id)}, event="ready")

    while True:
        entries = await read(redis, endpoint_id, cursor, BLOCK_MS)

        if not entries:
            # An SSE comment. Proxies and load balancers close idle connections,
            # and this also surfaces a client that went away: the write fails and
            # the generator is cancelled, releasing the Redis connection.
            yield ": ping\n\n"
            continue

        for entry_id, payload in entries:
            cursor = entry_id
            if payload["id"] in delivered:
                continue  # already handed over by the backfill
            yield _sse(payload, event="request", event_id=payload["id"])


@router.get("/stream")
async def stream_requests(
    request: Request,
    endpoint: DetachedEndpointDep,
    redis: RedisDep,
    last_event_id: int | None = Query(
        default=None,
        description="Resume after this capture id. `EventSource` sends the "
        "equivalent Last-Event-ID header automatically.",
    ),
) -> StreamingResponse:
    """Stream captures as they arrive.

    SSE rather than WebSockets on purpose: the traffic is one-way, it survives
    proxies that mangle upgrade handshakes, and the browser handles reconnection
    and resume itself. A WebSocket would mean writing all of that by hand for a
    channel nobody writes back on.
    """
    return StreamingResponse(
        _events(redis, endpoint.id, _requested_cursor(request, last_event_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Belt and braces against a buffering reverse proxy. With buffering on,
            # events pile up until the buffer fills and the feed arrives in bursts
            # or not at all -- and it debugs terribly, because the code is correct
            # and only the deployment is wrong.
            "X-Accel-Buffering": "no",
        },
    )
