"""When to try a failed delivery again, and when to stop.

Pure functions with the clock and the randomness passed in. That is not purity
for its own sake: exponential backoff reaching an hour cannot be tested by
waiting, and a jittered delay cannot be asserted on unless the randomness is
chosen by the test. Injecting both turns a twenty-minute suite into a
millisecond one.
"""

import random
from dataclasses import dataclass

# Retrying forever is not persistence, it is a leak: the queue grows without
# bound and a permanently dead destination is retried until the heat death of
# the universe. Eight attempts spread over roughly a day is long enough to ride
# out a real outage and short enough to admit defeat.
MAX_ATTEMPTS = 8


@dataclass(frozen=True)
class RetryPolicy:
    """How aggressively to retry, and how much to spread the retries out."""

    base_seconds: float = 5.0
    factor: float = 3.0
    max_seconds: float = 6 * 60 * 60
    max_attempts: int = MAX_ATTEMPTS
    # Fraction of the delay left to chance. At 0.5 the actual wait lands anywhere
    # in the top half of the window: never shorter than half the nominal delay,
    # never longer than the whole of it.
    jitter: float = 0.5


DEFAULT_POLICY = RetryPolicy()


def delay_for(attempt: int, policy: RetryPolicy = DEFAULT_POLICY, roll: float = 0.5) -> float:
    """Seconds to wait before attempt number `attempt + 1`.

    Exponential, capped, and jittered.

    The jitter is the part that is not decoration. When a destination goes down,
    every delivery to it fails within the same few seconds -- and without jitter
    every one of them retries at the same instant too. The destination comes back
    up, gets hit by five hundred simultaneous requests, and falls over again. That
    is the thundering herd, and the fix is to smear the retries across a window
    instead of aligning them on one.

    `roll` is a value in [0, 1) supplied by the caller so a test can pin it.
    """
    if attempt < 1:
        raise ValueError("attempt is 1-based")

    nominal = min(policy.base_seconds * policy.factor ** (attempt - 1), policy.max_seconds)
    return nominal * (1 - policy.jitter + policy.jitter * roll)


def next_delay(
    attempt: int, policy: RetryPolicy = DEFAULT_POLICY, rng: random.Random | None = None
) -> float:
    """`delay_for` with real randomness, for production callers.

    `random` and not `secrets`, deliberately. Jitter is not a secret and nothing
    is protected by it being unpredictable: its whole job is to keep retries from
    landing on the same instant. A cryptographic generator would be slower and
    would say something untrue about the intent.
    """
    roll = rng.random() if rng is not None else random.random()  # noqa: S311
    return delay_for(attempt, policy, roll)


def is_exhausted(attempts: int, policy: RetryPolicy = DEFAULT_POLICY) -> bool:
    """Whether the delivery has run out of attempts and belongs in the dead letters."""
    return attempts >= policy.max_attempts


def should_retry(status_code: int | None) -> bool:
    """Whether this outcome is worth trying again.

    `None` means the request never got an answer -- a timeout or a refused
    connection -- which is exactly the transient case retries exist for.

    A 4xx other than 408 and 429 means the destination understood us and said no.
    Repeating an identical request will get an identical refusal, so retrying
    only wastes attempts and hides the real problem behind "still pending".
    """
    if status_code is None:
        return True

    if status_code in (408, 429):  # timeout, rate limited: explicitly "try later"
        return True

    return status_code >= 500
