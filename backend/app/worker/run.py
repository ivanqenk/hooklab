"""The delivery worker: a loop that drains due deliveries.

Run it beside the web process:

    .venv/bin/python -m app.worker.run

Polling rather than a push queue, which follows from putting the queue in
Postgres. The cost is up to `IDLE_SLEEP` of latency on an idle system; the gain
is that the schedule and the state the user sees are the same rows, so they can
never disagree.

Each pass opens its own session and commits at the end. The claim holds row locks
for the length of that transaction, so batches are kept small and short: a long
transaction would park locks on deliveries another worker could be handling.
"""

import asyncio
import contextlib
import logging
import signal

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.worker.delivery import run_once

logger = logging.getLogger(__name__)

# How long to wait after finding nothing. Short enough that a webhook is
# forwarded promptly, long enough that an idle Hooklab is not hammering Postgres.
IDLE_SLEEP = 1.0


async def loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with SessionLocal() as session:
                processed = await run_once(session)
        except Exception:  # noqa: BLE001 - one bad batch must not kill the worker
            logger.exception("delivery batch failed")
            processed = 0

        if processed:
            # More may be waiting; go straight round again rather than sleeping
            # through a backlog one batch at a time.
            continue

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP)


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    stop = asyncio.Event()
    running = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Finish the batch in flight instead of dying mid-delivery, which would
        # leave a request sent and its outcome unrecorded.
        running.add_signal_handler(sig, stop.set)

    logger.info("delivery worker started")
    try:
        await loop(stop)
    finally:
        await engine.dispose()
        logger.info("delivery worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
