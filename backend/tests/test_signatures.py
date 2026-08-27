"""Tests for signature verification.

These are pure functions, so there is no database and no HTTP here. The clock is
passed in as `now`, which is what makes the timestamp cases instant instead of
requiring the suite to sleep for five minutes.
"""

import hashlib
import hmac

import pytest

from app.services import signatures
from app.services.signatures import Reason, Verification

# GitHub publishes this exact pair in its webhook documentation. Anchoring on an
# externally published vector is what makes this a real test: a signature we
# generated ourselves with our own code would agree with any bug we happened to
# write.
GITHUB_SECRET = "It's a Secret to Everybody"
GITHUB_BODY = b"Hello, World!"
GITHUB_SIGNATURE = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"

STRIPE_SECRET = "whsec_test_secret"
STRIPE_BODY = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
NOW = 1_614_556_800.0


def _stripe_header(timestamp: int, *bodies: bytes, secret: str = STRIPE_SECRET) -> str:
    """Build a Stripe-Signature header, one v1 per body handed in."""
    parts = [f"t={timestamp}"]
    for body in bodies:
        digest = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()
        parts.append(f"v1={digest}")
    return ",".join(parts)


def _verify(
    provider: str,
    secret: str,
    body: bytes,
    headers: dict[str, str],
    *,
    body_truncated: bool = False,
    body_size: int | None = None,
    now: float = NOW,
) -> Verification:
    """Call the dispatcher with the boring arguments filled in."""
    return signatures.verify(
        provider,
        secret,
        body,
        headers,
        body_truncated=body_truncated,
        body_size=len(body) if body_size is None else body_size,
        now=now,
    )


# --- GitHub ----------------------------------------------------------------


def test_github_accepts_its_own_documented_vector() -> None:
    result = _verify(
        "github", GITHUB_SECRET, GITHUB_BODY, {"x-hub-signature-256": GITHUB_SIGNATURE}
    )

    assert result.valid
    assert result.reason is Reason.VALID


def test_github_rejects_a_wrong_secret() -> None:
    result = _verify(
        "github", "not the secret", GITHUB_BODY, {"x-hub-signature-256": GITHUB_SIGNATURE}
    )

    assert not result.valid
    assert result.reason is Reason.MISMATCH


def test_github_rejects_a_body_altered_by_one_byte() -> None:
    """The whole point of a signature: one byte changes the digest completely."""
    result = _verify(
        "github", GITHUB_SECRET, b"Hello, World?", {"x-hub-signature-256": GITHUB_SIGNATURE}
    )

    assert not result.valid
    assert result.reason is Reason.MISMATCH


def test_github_reports_a_missing_header_as_missing() -> None:
    """Not as a mismatch: nothing was compared, so nothing can have failed."""
    result = _verify("github", GITHUB_SECRET, GITHUB_BODY, {})

    assert result.reason is Reason.MISSING_HEADER
    assert "secret configured" in result.detail


def test_github_names_the_deprecated_sha1_header() -> None:
    """A real mistake with a specific fix, so the diagnosis should say which."""
    result = _verify("github", GITHUB_SECRET, GITHUB_BODY, {"x-hub-signature-256": "sha1=abc123"})

    assert result.reason is Reason.MALFORMED_HEADER
    assert "X-Hub-Signature" in result.detail


# --- Stripe ----------------------------------------------------------------


def test_stripe_accepts_a_correct_signature() -> None:
    header = _stripe_header(int(NOW), STRIPE_BODY)

    result = _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header})

    assert result.valid


def test_stripe_signs_over_the_timestamp_and_the_body_not_the_body_alone() -> None:
    """The single most common Stripe integration mistake.

    Signing the body by itself produces a mismatch that looks exactly like a
    wrong secret, which is why people lose hours on it.
    """
    body_only = hmac.new(STRIPE_SECRET.encode(), STRIPE_BODY, hashlib.sha256).hexdigest()
    header = f"t={int(NOW)},v1={body_only}"

    result = _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header})

    assert not result.valid
    assert result.reason is Reason.MISMATCH


def test_stripe_accepts_any_of_several_signatures() -> None:
    """During a secret rotation Stripe signs with both keys at once.

    Reading only the first `v1` makes verification fail intermittently for the
    length of the rotation -- and then start working again on its own.
    """
    header = _stripe_header(int(NOW), b"signed with the old secret", STRIPE_BODY)

    result = _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header})

    assert result.valid


def test_stripe_calls_a_stale_event_stale_rather_than_invalid() -> None:
    """The diagnosis that makes this feature worth having.

    The secret is right and the signature is genuine; the event is simply old.
    Reporting "invalid signature" would send someone hunting a correct secret.
    """
    header = _stripe_header(int(NOW) - 400, STRIPE_BODY)

    result = _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header})

    assert not result.valid
    assert result.reason is Reason.TIMESTAMP_OUT_OF_TOLERANCE
    assert "your secret is correct" in result.detail
    assert "400 seconds old" in result.detail


def test_stripe_recognises_a_clock_running_ahead() -> None:
    """A timestamp in the future is skew on this machine, not a replay."""
    header = _stripe_header(int(NOW) + 400, STRIPE_BODY)

    result = _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header})

    assert result.reason is Reason.TIMESTAMP_OUT_OF_TOLERANCE
    assert "in the future" in result.detail


def test_stripe_accepts_a_timestamp_inside_the_tolerance() -> None:
    header = _stripe_header(int(NOW) - 299, STRIPE_BODY)

    assert _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header}).valid


def test_stripe_rejects_a_wrong_secret() -> None:
    header = _stripe_header(int(NOW), STRIPE_BODY, secret="whsec_someone_elses")

    result = _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header})

    assert result.reason is Reason.MISMATCH
    assert "test-mode" in result.detail


def test_stripe_reports_a_malformed_header() -> None:
    result = _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": "garbage"})

    assert result.reason is Reason.MALFORMED_HEADER


def test_stripe_ignores_schemes_it_does_not_know() -> None:
    """A future `v2` must not break verification of the `v1` that is present."""
    header = _stripe_header(int(NOW), STRIPE_BODY) + ",v0=deadbeef,v2=something"

    assert _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header}).valid


# --- Dispatcher ------------------------------------------------------------


def test_a_truncated_body_is_never_called_a_bad_signature() -> None:
    """The diagnosis no other tool can give, because no other tool truncated it.

    Hooklab cut the body at its own size limit, so the HMAC cannot match. Saying
    "invalid signature" would blame a secret that is perfectly fine.
    """
    result = _verify(
        "github",
        GITHUB_SECRET,
        GITHUB_BODY,
        {"x-hub-signature-256": GITHUB_SIGNATURE},
        body_truncated=True,
        body_size=1_048_576,
    )

    assert not result.valid
    assert result.reason is Reason.BODY_TRUNCATED
    assert "says nothing about your secret" in result.detail


def test_no_configured_secret_is_reported_as_such() -> None:
    result = _verify("github", "", GITHUB_BODY, {"x-hub-signature-256": GITHUB_SIGNATURE})

    assert result.reason is Reason.NO_SECRET


def test_an_unknown_provider_is_loud() -> None:
    """It is a bug on our side, not bad input: the name is validated on the way in.

    Reporting it as a failed signature would hide the mistake behind a plausible
    looking result.
    """
    with pytest.raises(KeyError):
        _verify("paypal", "secret", b"{}", {})


def test_every_registered_provider_is_reachable() -> None:
    assert set(signatures.PROVIDERS) == {"stripe", "github"}
    for provider in signatures.PROVIDERS:
        assert signatures.VERIFIERS[provider].name == provider


# --- Security --------------------------------------------------------------


def test_comparison_is_constant_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comparing digests with `==` leaks the correct signature.

    String equality returns at the first differing byte, so response time reveals
    how many leading characters were right; enough attempts reconstruct a valid
    signature one character at a time. This asserts `compare_digest` is actually
    reached, rather than trusting that nobody swaps it out later.
    """
    calls: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)

    _verify("github", GITHUB_SECRET, GITHUB_BODY, {"x-hub-signature-256": GITHUB_SIGNATURE})
    assert calls, "GitHub verification must go through compare_digest"

    calls.clear()
    header = _stripe_header(int(NOW), STRIPE_BODY)
    _verify("stripe", STRIPE_SECRET, STRIPE_BODY, {"stripe-signature": header})
    assert calls, "Stripe verification must go through compare_digest"


def test_the_secret_never_appears_in_a_diagnosis() -> None:
    """Diagnoses are shown in the UI and may be pasted into a bug report."""
    secret = "whsec_super_secret_value"
    results = [
        _verify("github", secret, GITHUB_BODY, {"x-hub-signature-256": GITHUB_SIGNATURE}),
        _verify("github", secret, GITHUB_BODY, {}),
        _verify("github", secret, GITHUB_BODY, {"x-hub-signature-256": "sha1=x"}),
        _verify("stripe", secret, STRIPE_BODY, {"stripe-signature": "garbage"}),
        _verify(
            "stripe", secret, STRIPE_BODY, {"stripe-signature": _stripe_header(int(NOW) - 400)}
        ),
    ]

    for result in results:
        assert secret not in result.detail
