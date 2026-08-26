"""Prove that the live feed survives more than one worker.

This is the check that justifies the whole real-time design, and it cannot be
done from the test suite: pytest drives the app in a single process, where a
plain in-memory list would pass every test. The bug only exists across
processes.

The shape of it: an SSE connection is served by ONE uvicorn worker, while the
webhooks land on whichever worker the kernel hands them to. Without a shared
bus the browser only ever sees the fraction that happened to arrive on its own
worker -- and with a single worker the failure does not reproduce at all, which
is what makes it so easy to ship.

Run it against a real multi-worker server:

    .venv/bin/uvicorn app.main:app --port 8010 --workers 4
    .venv/bin/python scripts/verify_fanout.py

Exits non-zero if any event is missing, so CI can run it too.
"""

import asyncio
import json
import shutil
import subprocess
import sys

import httpx

BASE = "http://127.0.0.1:8010"
WEBHOOKS = 20
READY_TIMEOUT = 10.0
EVENT_TIMEOUT = 15.0

# The kernel decides which worker accepts a connection, so one burst can land
# entirely on one process and prove nothing. Retry rather than report a false
# result in either direction.
ATTEMPTS = 5


def server_pids() -> set[str]:
    """PIDs holding an established connection whose SOURCE port is ours.

    Filtering on the source port is what separates the server side of a loopback
    connection from the client side. Without it, `ss` reports both ends -- and
    the client process gets mistaken for a worker.

    The binary is resolved to an absolute path rather than trusting PATH, which
    also doubles as the "is `ss` even installed?" check.
    """
    binary = shutil.which("ss")
    if binary is None:
        return set()

    try:
        output = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            [binary, "-tnp", "state", "established", "( sport = :8010 )"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return set()

    return {
        part.removeprefix("pid=")
        for line in output.splitlines()
        for part in line.replace(",", " ").split()
        if part.startswith("pid=")
    }


async def sample_workers(stop: asyncio.Event, into: set[str]) -> None:
    """Collect worker PIDs while the burst is in flight.

    Sampling once after `gather` returns is too late: those connections are
    already closed or idle and only the long-lived stream is left to see, which
    makes a perfectly good fan-out look like it all landed on one process.

    `to_thread` keeps the blocking `ss` call off the event loop, which would
    otherwise stall the very requests being measured.
    """
    while not stop.is_set():
        into |= await asyncio.to_thread(server_pids)
        await asyncio.sleep(0.01)


async def read_events(
    client: httpx.AsyncClient, view_token: str, ready: asyncio.Event, seen: list[int]
) -> None:
    """Hold one SSE connection open and collect capture ids."""
    async with client.stream("GET", f"{BASE}/api/endpoints/{view_token}/stream") as response:
        response.raise_for_status()
        event = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: ") and event == "ready":
                ready.set()
            elif line.startswith("data: ") and event == "request":
                seen.append(json.loads(line.removeprefix("data: "))["id"])
                if len(seen) >= WEBHOOKS:
                    return


async def run_once() -> tuple[bool, set[str], list[int]]:
    """One burst: open a stream, fire the webhooks, count what comes back."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        created = (await client.post(f"{BASE}/api/endpoints", json={"name": "fanout"})).json()

        ready = asyncio.Event()
        seen: list[int] = []
        reader = asyncio.create_task(read_events(client, created["view_token"], ready, seen))

        # Nothing is sent until the feed is actually listening: a webhook that
        # arrives before the stream is up would be a backfill test, not a fan-out
        # one, and would pass for the wrong reason.
        await asyncio.wait_for(ready.wait(), READY_TIMEOUT)
        sse_worker = await asyncio.to_thread(server_pids)

        # Concurrently, so the kernel really spreads them. Sent one after another
        # they can all land on a single worker -- possibly the very one holding
        # the stream -- and then the run proves nothing.
        during_burst: set[str] = set()
        stop = asyncio.Event()
        sampler = asyncio.create_task(sample_workers(stop, during_burst))

        url = f"{BASE}/in/{created['ingest_token']}"
        sent = await asyncio.gather(*(client.post(url, json={"n": n}) for n in range(WEBHOOKS)))

        stop.set()
        await sampler

        try:
            await asyncio.wait_for(reader, EVENT_TIMEOUT)
        except TimeoutError:
            reader.cancel()

    ok = all(r.status_code == 200 for r in sent)
    ok = ok and len(seen) == WEBHOOKS and len(set(seen)) == WEBHOOKS
    return ok, sse_worker | during_burst, seen


async def main() -> int:
    """Burst until the kernel has actually spread the load, then judge.

    Which worker accepts a connection is the kernel's call, so a single burst can
    legitimately land entirely on one process. That says nothing about the bus, so
    rather than passing on a run that proved nothing -- or failing a system that is
    fine -- the burst is simply repeated until the spread is observed.
    """
    workers: set[str] = set()

    for attempt in range(1, ATTEMPTS + 1):
        ok, seen_workers, seen = await run_once()
        workers |= seen_workers
        spread = len(seen_workers) > 1

        print(
            f"burst {attempt}: {len(seen)}/{WEBHOOKS} events, "
            f"{len(set(seen))} distinct ids, workers {sorted(seen_workers)}"
            f"{'' if spread else '  (all on one worker -- inconclusive, retrying)'}"
        )

        if not ok:
            print("FAIL: the stream did not receive every capture exactly once")
            return 1
        if spread:
            print(
                f"OK: {WEBHOOKS} captures crossed from workers {sorted(seen_workers)} "
                "to the one holding the stream"
            )
            return 0

    print(
        f"INCONCLUSIVE: delivery was complete every time, but across {ATTEMPTS} bursts every "
        f"connection landed on a single worker {sorted(workers)}. Is the server running "
        "with --workers > 1?"
    )
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
