"""Tests for the delivery worker.

Against real Postgres, because the interesting parts are the queue semantics --
which rows a worker claims, what it does when two of them run at once -- and
those live in the database, not in Python.

The clock is passed in and the sender is injected, so exponential backoff that
reaches hours is asserted on in microseconds.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models import Delivery, DeliveryState, Destination
from app.security.ssrf import SafeTarget
from app.services.outbound import Attempt
from app.worker import delivery as worker
from tests.tiny_server import TinyServer, answers

# A reference point slightly ahead of the real clock, not a fixed date. The
# ingest route enqueues with the real clock -- threading a test clock through the
# whole request path would distort the thing under test -- so a hard-coded date
# would sit in the past and nothing would ever look due. The margin only has to
# outlast the test itself; every assertion is relative to this point, never to a
# wall-clock value.
NOW = datetime.now(UTC) + timedelta(minutes=1)
TARGET = "https://destination.example/hooks"


def _pin(monkeypatch: pytest.MonkeyPatch, port: int = 9) -> None:
    """Skip the address rules, which have their own suite and refuse loopback."""
    monkeypatch.setattr(
        worker,
        "validate",
        lambda url: SafeTarget(
            url=url, scheme="http", host="destination.example", port=port, ip="127.0.0.1"
        ),
    )


def _replies(status: int | None, error: str | None = None) -> worker.Sender:
    async def sender(*args: object, **kwargs: object) -> Attempt:
        return Attempt(status, "", error, 5)

    return sender


async def _endpoint(client: AsyncClient) -> dict[str, str]:
    return dict((await client.post("/api/endpoints", json={})).json())


async def _destination(
    client: AsyncClient, db: AsyncSession, endpoint: dict[str, str], *, verified: bool = True
) -> Destination:
    """Insert the destination directly rather than through the API.

    The registration route is covered by its own suite, and going through it here
    would drag real DNS into every test in this file -- `validate` refuses a name
    that does not resolve, and waiting for that verdict is both slow and dependent
    on the network being up.
    """
    destination = Destination(
        endpoint_id=uuid.UUID(endpoint["id"]),
        target_url=TARGET,
        verified_at=(NOW - timedelta(days=1)) if verified else None,
    )
    db.add(destination)
    await db.commit()
    await db.refresh(destination)
    return destination


async def _capture(client: AsyncClient, endpoint: dict[str, str]) -> int:
    response = await client.post(f"/in/{endpoint['ingest_token']}", json={"event": "ping"})
    request_id: int = response.json()["request_id"]
    return request_id


async def _deliveries(db: AsyncSession) -> list[Delivery]:
    return list((await db.execute(select(Delivery).order_by(Delivery.id))).scalars().all())


# --- Queueing --------------------------------------------------------------


async def test_a_capture_is_queued_for_a_verified_destination(
    client: AsyncClient, db: AsyncSession
) -> None:
    endpoint = await _endpoint(client)
    destination = await _destination(client, db, endpoint)

    await _capture(client, endpoint)

    queued = await _deliveries(db)
    assert len(queued) == 1
    assert queued[0].destination_id == destination.id
    assert queued[0].state == DeliveryState.PENDING


async def test_an_unverified_destination_receives_nothing(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The anti-abuse rule, enforced where it actually matters.

    Until someone proves they control the URL, forwarding to it would make
    Hooklab an amplifier aimed at a stranger's server.
    """
    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint, verified=False)

    await _capture(client, endpoint)

    assert await _deliveries(db) == []


async def test_each_delivery_gets_its_own_idempotency_key(
    client: AsyncClient, db: AsyncSession
) -> None:
    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint)

    await _capture(client, endpoint)
    await _capture(client, endpoint)

    keys = {d.idempotency_key for d in await _deliveries(db)}
    assert len(keys) == 2


# --- The state machine -----------------------------------------------------


async def test_a_2xx_marks_the_delivery_delivered(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint)
    await _capture(client, endpoint)

    async with SessionLocal() as session:
        assert await worker.run_once(session, NOW, _replies(200)) == 1

    delivery = (await _deliveries(db))[0]
    assert delivery.state == DeliveryState.DELIVERED
    assert delivery.attempts == 1
    assert delivery.next_attempt_at is None
    assert delivery.delivered_at is not None


async def test_a_500_schedules_a_retry(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint)
    await _capture(client, endpoint)

    async with SessionLocal() as session:
        await worker.run_once(session, NOW, _replies(500))

    delivery = (await _deliveries(db))[0]
    assert delivery.state == DeliveryState.FAILED
    assert delivery.attempts == 1
    assert delivery.next_attempt_at is not None
    assert delivery.next_attempt_at > NOW
    assert delivery.last_status_code == 500


async def test_the_wait_grows_between_attempts(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backing off gives a struggling destination progressively more room."""
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint)
    await _capture(client, endpoint)

    waits = []
    moment = NOW
    for _ in range(4):
        async with SessionLocal() as session:
            await worker.run_once(session, moment, _replies(503))
        delivery = (await _deliveries(db))[0]
        await db.refresh(delivery)
        assert delivery.next_attempt_at is not None
        waits.append((delivery.next_attempt_at - moment).total_seconds())
        moment = delivery.next_attempt_at

    assert waits == sorted(waits), f"delays must not shrink: {waits}"
    assert waits[-1] > waits[0]


async def test_a_400_gives_up_immediately(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The destination understood us and said no.

    Repeating an identical request earns an identical refusal; spending eight
    attempts on it just hides a permanent problem behind "pending" for hours.
    """
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint)
    await _capture(client, endpoint)

    async with SessionLocal() as session:
        await worker.run_once(session, NOW, _replies(400))

    delivery = (await _deliveries(db))[0]
    assert delivery.state == DeliveryState.EXHAUSTED
    assert delivery.next_attempt_at is None
    assert delivery.last_error is not None
    assert "refused it" in delivery.last_error


async def test_running_out_of_attempts_lands_in_the_dead_letters(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retrying forever is a leak, not persistence."""
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    destination = await _destination(client, db, endpoint)
    destination.max_attempts = 3
    await db.commit()
    await _capture(client, endpoint)

    moment = NOW
    for _ in range(3):
        async with SessionLocal() as session:
            await worker.run_once(session, moment, _replies(500))
        delivery = (await _deliveries(db))[0]
        await db.refresh(delivery)
        moment = delivery.next_attempt_at or moment + timedelta(hours=1)

    assert delivery.state == DeliveryState.EXHAUSTED
    assert delivery.attempts == 3
    assert delivery.next_attempt_at is None


async def test_a_refused_address_never_reaches_the_network(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The address is checked again immediately before connecting.

    Trusting the check made when the destination was registered leaves a window:
    DNS can change in between, innocently or on purpose.
    """
    from app.security.ssrf import UnsafeTarget

    def refuse(url: str) -> SafeTarget:
        raise UnsafeTarget("resolves to 127.0.0.1, which is a loopback address")

    monkeypatch.setattr(worker, "validate", refuse)

    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint)
    await _capture(client, endpoint)

    sent = False

    async def must_not_run(*args: object, **kwargs: object) -> Attempt:
        nonlocal sent
        sent = True
        return Attempt(200, "", None, 1)

    async with SessionLocal() as session:
        await worker.run_once(session, NOW, must_not_run)

    assert not sent, "a refused address must not open a connection"
    delivery = (await _deliveries(db))[0]
    assert delivery.state == DeliveryState.EXHAUSTED
    assert delivery.last_error is not None
    assert "loopback" in delivery.last_error


# --- The circuit breaker ---------------------------------------------------


async def test_a_destination_that_keeps_failing_gets_paused(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Our reliability must not become a denial of service against a site that
    is already down. After a run of failures it is left alone for a while.
    """
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    destination = await _destination(client, db, endpoint)

    moment = NOW
    for _ in range(worker.CIRCUIT_THRESHOLD):
        await _capture(client, endpoint)
        async with SessionLocal() as session:
            await worker.run_once(session, moment, _replies(500))
        moment += timedelta(seconds=1)

    await db.refresh(destination)
    assert destination.consecutive_failures >= worker.CIRCUIT_THRESHOLD
    assert destination.paused_until is not None


async def test_a_paused_destination_is_skipped_until_the_pause_expires(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    destination = await _destination(client, db, endpoint)
    await _capture(client, endpoint)

    destination.paused_until = NOW + timedelta(minutes=10)
    await db.commit()

    async with SessionLocal() as session:
        assert await worker.run_once(session, NOW, _replies(200)) == 0

    async with SessionLocal() as session:
        later = NOW + timedelta(minutes=11)
        assert await worker.run_once(session, later, _replies(200)) == 1


async def test_one_success_clears_the_breaker(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The breaker measures a run of failures, not a lifetime total."""
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    destination = await _destination(client, db, endpoint)
    destination.consecutive_failures = 3
    await db.commit()
    await _capture(client, endpoint)

    async with SessionLocal() as session:
        await worker.run_once(session, NOW, _replies(200))

    await db.refresh(destination)
    assert destination.consecutive_failures == 0
    assert destination.paused_until is None


# --- Concurrency -----------------------------------------------------------


async def test_two_workers_never_take_the_same_delivery(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What `SKIP LOCKED` buys.

    Without it, two workers either serialise into one -- the second blocking
    behind the first -- or both claim the same row and forward it twice.
    """
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint)
    for _ in range(4):
        await _capture(client, endpoint)

    async with SessionLocal() as first, SessionLocal() as second:
        mine = await worker.claim_due(first, NOW, limit=2)
        theirs = await worker.claim_due(second, NOW, limit=4)

        assert len(mine) == 2
        # The two rows the first worker holds are stepped over, not waited on.
        assert len(theirs) == 2
        assert {d.id for d, _, _ in mine}.isdisjoint({d.id for d, _, _ in theirs})


# --- Headers ---------------------------------------------------------------


async def test_the_forwarded_request_keeps_the_providers_headers(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signature headers above all: the destination has to be able to verify the
    signature itself, and it cannot if we rewrite what it was computed over.
    """
    endpoint = await _endpoint(client)
    destination = await _destination(client, db, endpoint)
    await client.post(
        f"/in/{endpoint['ingest_token']}",
        content=b'{"event":"ping"}',
        headers={
            "content-type": "application/json",
            "x-hub-signature-256": "sha256=abc",
            "content-length": "16",
        },
    )

    async with TinyServer(answers(200, b"ok")) as server:
        _pin(monkeypatch, server.port)
        async with SessionLocal() as session:
            await worker.run_once(session, NOW)

    received = server.received[0]
    assert received.headers["x-hub-signature-256"] == "sha256=abc"
    assert received.body == b'{"event":"ping"}'
    # Hop-by-hop headers describe the old connection; forwarding a stale
    # Content-Length truncates the body, and the old Host points at Hooklab.
    assert received.headers["host"] == f"destination.example:{server.port}"

    await db.refresh(destination)
    assert (await _deliveries(db))[0].state == DeliveryState.DELIVERED


async def test_the_idempotency_key_travels_and_does_not_change(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The receiver's only defence against the duplicates HTTP makes inevitable.

    A fresh key per attempt would make it useless, and that is the easy mistake.
    """
    _pin(monkeypatch)
    endpoint = await _endpoint(client)
    await _destination(client, db, endpoint)
    await _capture(client, endpoint)

    seen: list[str] = []

    async def record(*args: object, **kwargs: object) -> Attempt:
        headers = kwargs["headers"]
        assert isinstance(headers, dict)
        seen.append(headers["x-hooklab-idempotency-key"])
        return Attempt(500, "", None, 1)

    moment = NOW
    for _ in range(3):
        async with SessionLocal() as session:
            await worker.run_once(session, moment, record)
        delivery = (await _deliveries(db))[0]
        await db.refresh(delivery)
        moment = delivery.next_attempt_at or moment

    assert len(seen) == 3
    assert len(set(seen)) == 1, "the key must be identical on every attempt"
    assert seen[0] == str(delivery.idempotency_key)
