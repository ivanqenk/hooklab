"""Forwarding captures to their destinations, with retries.

**The queue is Postgres, not a task queue.** The plan called for ARQ, and this is
a deliberate departure. Every fact a job queue would hold -- when the next attempt
is due, how many have been made, what went wrong last time -- already has to live
in `deliveries`, because the UI shows exactly that. Running ARQ alongside would
mean two schedules that can disagree, and the one users see would be the one that
is wrong. `SELECT ... FOR UPDATE SKIP LOCKED` gives a queue over the table we
already keep, with a single source of truth, and it survives a Redis restart.

`SKIP LOCKED` is what makes several workers safe: each claims rows nobody else
holds and steps over the ones already taken, instead of blocking behind them.
Without it, two workers either serialise into one, or both grab the same delivery
and forward it twice.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapturedRequest, Delivery, DeliveryState, Destination
from app.security.ssrf import UnsafeTarget, validate
from app.services.outbound import Attempt, send
from app.worker.retry import RetryPolicy, next_delay, should_retry

logger = logging.getLogger(__name__)

# How many consecutive failures before a destination is left alone for a while.
# Without this, a destination that is down gets hit by every retry of every
# capture -- our reliability turns into a denial of service against a site that
# is already having a bad day.
CIRCUIT_THRESHOLD = 5
CIRCUIT_PAUSE = timedelta(minutes=5)

BATCH_SIZE = 20

# Headers that describe *this* connection rather than the message. Forwarding
# them corrupts the new request: a stale Content-Length truncates the body, and
# the original Host points at Hooklab rather than the destination.
HOP_BY_HOP = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "te",
        "trailer",
        "upgrade",
        "proxy-authorization",
        "proxy-authenticate",
        "expect",
    }
)

Sender = Callable[..., Awaitable[Attempt]]


def forwarded_headers(
    captured: CapturedRequest, destination: Destination, delivery: Delivery
) -> dict[str, str]:
    """The headers the destination receives.

    The provider's own headers are passed through untouched -- signature headers
    above all. The whole point is that the destination can verify the signature
    itself, and it cannot do that if we rewrite what it was computed over.
    """
    headers = {
        name: value for name, value in captured.headers.items() if name.lower() not in HOP_BY_HOP
    }

    headers.update(destination.extra_headers or {})

    # Same value on every attempt of this delivery, which is what lets the
    # receiver recognise a repeat. At-least-once delivery is unavoidable over
    # HTTP; deduplication on the receiving end is what makes it liveable.
    headers["x-hooklab-idempotency-key"] = str(delivery.idempotency_key)
    headers["x-hooklab-delivery-id"] = str(delivery.id)
    headers["x-hooklab-request-id"] = str(captured.id)
    headers["x-hooklab-attempt"] = str(delivery.attempts + 1)

    return headers


async def enqueue(session: AsyncSession, captured: CapturedRequest) -> list[Delivery]:
    """Queue this capture for every destination that is allowed to receive it.

    Unverified destinations are skipped: until someone has proven they control
    that URL, forwarding to it would make Hooklab an amplifier pointed at a
    stranger.

    Runs inside the capture's own transaction, so a failed commit leaves no
    delivery pointing at a request that does not exist.
    """
    result = await session.execute(
        select(Destination).where(
            Destination.endpoint_id == captured.endpoint_id,
            Destination.active.is_(True),
            Destination.verified_at.is_not(None),
        )
    )

    deliveries = [
        Delivery(
            request_id=captured.id,
            destination_id=destination.id,
            state=DeliveryState.PENDING,
            next_attempt_at=datetime.now(UTC),
        )
        for destination in result.scalars().all()
    ]
    session.add_all(deliveries)

    return deliveries


async def claim_due(
    session: AsyncSession, now: datetime, limit: int = BATCH_SIZE
) -> list[tuple[Delivery, Destination, CapturedRequest]]:
    """Take ownership of deliveries that are due, skipping anyone else's.

    Rows stay locked until the caller's transaction ends, so a second worker
    running at the same instant simply picks different ones.
    """
    statement = (
        select(Delivery, Destination, CapturedRequest)
        .join(Destination, Destination.id == Delivery.destination_id)
        .join(CapturedRequest, CapturedRequest.id == Delivery.request_id)
        .where(
            Delivery.next_attempt_at.is_not(None),
            Delivery.next_attempt_at <= now,
            Destination.active.is_(True),
            Destination.verified_at.is_not(None),
            # A destination inside its circuit-breaker pause is left alone; its
            # deliveries stay due and get picked up once the pause expires.
            or_(Destination.paused_until.is_(None), Destination.paused_until <= now),
        )
        .order_by(Delivery.next_attempt_at)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Delivery)
    )

    return [(d, dest, req) for d, dest, req in (await session.execute(statement)).all()]


def _record_success(
    delivery: Delivery, destination: Destination, attempt: Attempt, now: datetime
) -> None:
    delivery.state = DeliveryState.DELIVERED
    delivery.delivered_at = now
    delivery.next_attempt_at = None
    delivery.last_status_code = attempt.status_code
    delivery.last_error = None
    # One success clears the record: the breaker measures a run of failures, not
    # a lifetime total.
    destination.consecutive_failures = 0
    destination.paused_until = None


def _record_failure(
    delivery: Delivery,
    destination: Destination,
    attempt: Attempt,
    now: datetime,
    policy: RetryPolicy,
) -> None:
    delivery.attempts += 1
    delivery.last_status_code = attempt.status_code
    delivery.last_error = attempt.error or f"The destination answered {attempt.status_code}."

    destination.consecutive_failures += 1
    if destination.consecutive_failures >= CIRCUIT_THRESHOLD:
        destination.paused_until = now + CIRCUIT_PAUSE

    retryable = should_retry(attempt.status_code)
    out_of_attempts = delivery.attempts >= destination.max_attempts

    if not retryable or out_of_attempts:
        # Dead letters: visible, and retryable by hand once the cause is fixed.
        delivery.state = DeliveryState.EXHAUSTED
        delivery.next_attempt_at = None
        if not retryable:
            delivery.last_error += (
                " The destination understood the request and refused it, so retrying"
                " would only repeat the refusal."
            )
        return

    delivery.state = DeliveryState.FAILED
    delivery.next_attempt_at = now + timedelta(seconds=next_delay(delivery.attempts, policy))


async def attempt_delivery(
    delivery: Delivery,
    destination: Destination,
    captured: CapturedRequest,
    now: datetime,
    sender: Sender = send,
    policy: RetryPolicy | None = None,
) -> Attempt:
    """Try once, and write down what happened.

    The address is validated again right here. Trusting the check made when the
    destination was registered would leave a window: DNS can change in between,
    and the only check that means anything is the one immediately before the
    connection is opened.
    """
    policy = policy or RetryPolicy(max_attempts=destination.max_attempts)

    try:
        target = validate(destination.target_url)
    except UnsafeTarget as exc:
        # Not a transient failure, and not the destination's fault either: the
        # URL is not one we are willing to open a socket to.
        attempt = Attempt(None, "", f"Refused before connecting: {exc}", 0)
        _record_failure(delivery, destination, attempt, now, policy)
        delivery.state = DeliveryState.EXHAUSTED
        delivery.next_attempt_at = None
        return attempt

    attempt = await sender(
        target,
        method=captured.method,
        headers=forwarded_headers(captured, destination, delivery),
        body=captured.body_raw or b"",
        timeout_ms=destination.timeout_ms,
    )

    if attempt.status_code is not None and 200 <= attempt.status_code < 300:
        delivery.attempts += 1
        _record_success(delivery, destination, attempt, now)
    else:
        _record_failure(delivery, destination, attempt, now, policy)

    return attempt


async def run_once(
    session: AsyncSession,
    now: datetime | None = None,
    sender: Sender = send,
    limit: int = BATCH_SIZE,
) -> int:
    """Process one batch of due deliveries. Returns how many were attempted."""
    moment = now or datetime.now(UTC)
    claimed = await claim_due(session, moment, limit)

    for delivery, destination, captured in claimed:
        attempt = await attempt_delivery(delivery, destination, captured, moment, sender)
        logger.info(
            "delivery %s to %s: %s",
            delivery.id,
            destination.id,
            attempt.status_code or attempt.error,
        )

    await session.commit()
    return len(claimed)
