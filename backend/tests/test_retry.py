"""Tests for the retry policy.

No sleeping anywhere. The randomness is passed in, so a jittered delay that
would otherwise be untestable becomes an exact number.
"""

import random

import pytest

from app.worker.retry import (
    DEFAULT_POLICY,
    RetryPolicy,
    delay_for,
    is_exhausted,
    next_delay,
    should_retry,
)


def test_delays_grow_with_each_attempt() -> None:
    """Backing off is the point: a struggling destination gets more room each time."""
    delays = [delay_for(n, roll=1.0) for n in range(1, 6)]

    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


def test_the_delay_is_capped() -> None:
    """Unbounded exponential growth reaches years, which is the same as never."""
    policy = RetryPolicy(base_seconds=5, factor=3, max_seconds=60)

    assert delay_for(20, policy, roll=1.0) == 60


def test_jitter_spreads_retries_across_a_window() -> None:
    """The thundering-herd defence, and the reason jitter is not decoration.

    Five hundred deliveries fail together when a destination goes down. Without
    jitter they all retry at the same instant, and knock it over again the moment
    it recovers.
    """
    lowest = delay_for(3, roll=0.0)
    highest = delay_for(3, roll=0.999)

    assert lowest < highest
    # Never shorter than half the nominal delay, never longer than all of it.
    nominal = DEFAULT_POLICY.base_seconds * DEFAULT_POLICY.factor**2
    assert lowest == pytest.approx(nominal * 0.5)
    assert highest == pytest.approx(nominal, rel=0.01)


def test_the_spread_is_actually_used() -> None:
    """A policy that computed jitter and then ignored it would pass the test above."""
    # Seeded so the test is deterministic; jitter needs no cryptographic strength.
    rng = random.Random(1234)  # noqa: S311
    delays = {next_delay(4, rng=rng) for _ in range(50)}

    assert len(delays) > 40, "jittered delays must not collapse onto one value"


def test_attempt_numbering_is_one_based() -> None:
    """The first retry is attempt 1, not 0, and a zero is a bug worth catching."""
    assert delay_for(1, roll=1.0) == DEFAULT_POLICY.base_seconds

    with pytest.raises(ValueError, match="1-based"):
        delay_for(0)


def test_a_delivery_runs_out_of_attempts() -> None:
    """Retrying forever is a leak, not persistence."""
    assert not is_exhausted(DEFAULT_POLICY.max_attempts - 1)
    assert is_exhausted(DEFAULT_POLICY.max_attempts)
    assert is_exhausted(DEFAULT_POLICY.max_attempts + 1)


@pytest.mark.parametrize(
    ("status", "retry", "why"),
    [
        (None, True, "no answer at all: a timeout or refused connection"),
        (500, True, "the destination broke"),
        (502, True, "a bad gateway in front of it"),
        (503, True, "explicitly unavailable"),
        (408, True, "the destination asked for a retry by timing out"),
        (429, True, "rate limited, which means later, not never"),
        (200, False, "delivered"),
        (400, False, "the destination understood us and said no"),
        (401, False, "credentials will not fix themselves on a retry"),
        (404, False, "the path does not exist and will not appear"),
        (410, False, "gone, permanently"),
    ],
)
def test_only_transient_failures_are_retried(status: int | None, retry: bool, why: str) -> None:
    """Repeating a request the destination already refused wastes every attempt.

    Worse, it hides a permanent problem behind "still pending" for hours.
    """
    assert should_retry(status) is retry, why
