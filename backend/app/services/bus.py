"""The live event bus, on top of Redis Streams.

Why a bus exists at all: an SSE connection lives in one uvicorn worker and the
webhook that should feed it may land in a different one. Separate processes share
no memory, so without a broker the browser simply never sees the request. With a
single worker the bug does not show, which is what makes it treacherous.

Why Streams and not pub/sub: pub/sub is fire-and-forget. A subscriber that is
reconnecting at that instant loses the message with no way to notice. A Stream is
an ordered log, so "give me everything after id X" is a native operation -- which
is exactly what the SSE `Last-Event-ID` header asks for.

Postgres remains the durable record. The Stream is only a live buffer: entries
fall off it by design, and anything missing is refilled from the database.
"""

import json
import logging
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# Entries kept per endpoint. Bounded on purpose: without a cap, one abusive
# endpoint could grow a stream until Redis runs out of memory.
#
# `approximate=True` sends `MAXLEN ~ N`, which lets Redis trim only at internal
# node boundaries (~100 entries) instead of walking the structure on every write.
# It is far cheaper, at the price of keeping somewhat MORE than N -- never fewer,
# so it can never drop an entry that should still be there.
STREAM_MAXLEN = 1000
STREAM_APPROXIMATE = True

# The single field each entry carries. One JSON blob rather than a field per
# attribute keeps the payload shape defined in exactly one place: the schema.
PAYLOAD_FIELD = b"data"


def stream_key(endpoint_id: UUID) -> str:
    """One stream per endpoint, so a reader only ever sees its own traffic."""
    return f"ep:{endpoint_id}"


async def publish(redis: Redis, endpoint_id: UUID, payload: dict[str, Any]) -> None:
    """Announce a capture on the endpoint's stream.

    Failures are logged and swallowed. This is called after the capture is
    already committed to Postgres, so a Redis outage costs the live update but
    never the data: a reader that reconnects refills from the database. Letting
    the exception escape would turn a degraded live view into a failed ingest,
    and the provider would start retrying a webhook that was in fact stored.
    """
    try:
        await redis.xadd(
            stream_key(endpoint_id),
            {PAYLOAD_FIELD: json.dumps(payload).encode()},
            maxlen=STREAM_MAXLEN,
            approximate=STREAM_APPROXIMATE,
        )
    except Exception:  # noqa: BLE001 - a live-update failure must not break ingest
        logger.warning("could not publish to the stream of %s", endpoint_id, exc_info=True)


async def current_position(redis: Redis, endpoint_id: UUID) -> bytes:
    """The id of the newest entry, to be used as a starting cursor.

    A reader takes this BEFORE backfilling from Postgres. Anything that arrives
    during the backfill lands after this mark, so `XREAD` still delivers it. Done
    the other way round -- backfill first, subscribe second -- whatever arrives in
    between is lost for good.

    On an empty stream it returns `0-0`, meaning "from the beginning". That is
    safe precisely because there is nothing before it to replay.
    """
    entries = await redis.xrevrange(stream_key(endpoint_id), count=1)
    if not entries:
        return b"0-0"

    entry_id: bytes = entries[0][0]
    return entry_id


async def read(
    redis: Redis, endpoint_id: UUID, cursor: bytes, block_ms: int
) -> list[tuple[bytes, dict[str, Any]]]:
    """Wait for entries newer than `cursor`.

    Blocks inside Redis rather than polling in a loop: the connection is woken up
    when something is written, so an idle endpoint costs nothing and a busy one
    has no added latency. Returns an empty list when the block times out, which is
    the caller's cue to send a heartbeat.
    """
    response = await redis.xread({stream_key(endpoint_id): cursor}, block=block_ms)
    if not response:
        return []

    _, entries = response[0]
    return [(entry_id, json.loads(fields[PAYLOAD_FIELD])) for entry_id, fields in entries]
