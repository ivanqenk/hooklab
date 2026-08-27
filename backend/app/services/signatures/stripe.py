"""Stripe webhook signatures.

Richer than GitHub's, and the extra pieces are exactly where people get stuck:

    Stripe-Signature: t=1614556800,v1=5257a869e7...,v1=a6f0a4b1c2...

Two differences that matter:

1. **The signed payload is `{timestamp}.{raw_body}`, not the body alone.** Signing
   the body by itself is the classic mistake and produces a mismatch that looks
   exactly like a wrong secret.
2. **`v1` can appear more than once.** During a secret rotation Stripe signs with
   both the old and the new one, so any single match is a pass. Reading only the
   first `v1` makes verification fail intermittently for the duration of a
   rotation -- a wonderfully confusing bug.

The timestamp exists to bound replay attacks: a captured request cannot be
resent days later, because the signature covers when it was made.
"""

import hashlib
import hmac

from app.services.signatures.base import Reason, Verification, matches

# Stripe's own default tolerance. Five minutes is generous enough for a slow
# network and tight enough that a stolen request is not replayable for long.
DEFAULT_TOLERANCE_SECONDS = 300


class StripeVerifier:
    name = "stripe"
    header = "stripe-signature"

    def __init__(self, tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS) -> None:
        self.tolerance_seconds = tolerance_seconds

    def verify(self, body: bytes, headers: dict[str, str], secret: str, now: float) -> Verification:
        raw = headers.get(self.header)

        if raw is None:
            return Verification.failed(
                Reason.MISSING_HEADER,
                f"No {self.header} header arrived. If you are replaying a request "
                "by hand, note that the header has to be copied along with the "
                "body -- it cannot be regenerated without the secret.",
            )

        timestamp, signatures = _parse(raw)

        if timestamp is None or not signatures:
            return Verification.failed(
                Reason.MALFORMED_HEADER,
                "The header should look like 't=<unix seconds>,v1=<hex>' and it "
                f"arrived as '{raw[:48]}'. Both parts are required.",
            )

        # The signed payload is the timestamp, a literal dot, and the exact body
        # bytes. Building it as text would force a decode that mangles any body
        # which is not valid UTF-8.
        expected = hmac.new(
            secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
        ).hexdigest()

        signature_ok = any(matches(expected, candidate) for candidate in signatures)
        age = now - timestamp

        # The HMAC is checked FIRST so the diagnosis can tell two very different
        # situations apart. A genuine signature on a stale event means the secret
        # is right and the event is old -- usually a manual replay or a skewed
        # clock. Reporting that as "invalid signature" would send someone hunting
        # a secret that was never wrong.
        if signature_ok and abs(age) > self.tolerance_seconds:
            direction = "old" if age > 0 else "in the future"
            return Verification.failed(
                Reason.TIMESTAMP_OUT_OF_TOLERANCE,
                f"The signature is genuine, so your secret is correct, but the "
                f"timestamp is {abs(age):.0f} seconds {direction} and the "
                f"tolerance is {self.tolerance_seconds}. Either this is a replay "
                "of an earlier event, or this machine's clock has drifted.",
            )

        if not signature_ok:
            return Verification.failed(
                Reason.MISMATCH,
                "The HMAC does not match. In order of likelihood: a test-mode "
                "secret was used with a live-mode event (or the other way "
                "round), the secret belongs to another webhook endpoint, or the "
                "payload was signed over the body alone instead of over "
                "'{timestamp}.{body}'.",
            )

        return Verification.ok()


def _parse(header: str) -> tuple[int | None, list[str]]:
    """Pull the timestamp and every v1 signature out of the header.

    Unknown schemes (`v0`, and whatever Stripe adds next) are ignored rather than
    treated as an error, so a future addition does not break verification.
    """
    timestamp: int | None = None
    signatures: list[str] = []

    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None, []
        elif key == "v1":
            signatures.append(value)

    return timestamp, signatures
