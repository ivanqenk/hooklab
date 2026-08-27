"""The shared shape of a signature verifier.

Every provider signs differently -- hex against base64, the body alone against a
timestamp concatenated with it, one header against several -- but the question
being asked is always the same, so the answer has a single shape.

The point of this module is the answer type. Returning a bare boolean is what
every other tool does, and it is useless: the developer already knew something was
wrong. What they need is *which* of a handful of concrete mistakes they made, and
that is what `Verification` carries.
"""

import hmac
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Reason(StrEnum):
    """Why a verification came out the way it did.

    Machine-readable, so the UI can pick an icon and the API can be filtered on
    it, while `detail` carries the sentence a human actually reads.
    """

    VALID = "valid"
    # Not credentials: enum members naming the cases where a usable secret is
    # missing. UNREADABLE_SECRET means the stored ciphertext would not open --
    # essentially always because SECRET_KEY was rotated -- and it is kept distinct
    # from NO_SECRET because the fix is different: set the secret again, rather
    # than set one for the first time.
    NO_SECRET = "no_secret"  # noqa: S105
    UNREADABLE_SECRET = "unreadable_secret"  # noqa: S105
    MISSING_HEADER = "missing_header"
    MALFORMED_HEADER = "malformed_header"
    BODY_TRUNCATED = "body_truncated"
    TIMESTAMP_OUT_OF_TOLERANCE = "timestamp_out_of_tolerance"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class Verification:
    """The outcome of checking one capture against one secret."""

    valid: bool
    reason: Reason
    detail: str

    @classmethod
    def ok(cls, detail: str = "Signature matches.") -> "Verification":
        return cls(valid=True, reason=Reason.VALID, detail=detail)

    @classmethod
    def failed(cls, reason: Reason, detail: str) -> "Verification":
        return cls(valid=False, reason=reason, detail=detail)


class Verifier(Protocol):
    """What every provider implementation has to offer."""

    name: str
    header: str

    def verify(self, body: bytes, headers: dict[str, str], secret: str, now: float) -> Verification:
        """Check one capture.

        `body` is the exact bytes as they arrived -- parsing and re-serialising
        would change them and break every signature.

        `now` is passed in rather than read from the clock so that time-sensitive
        schemes can be tested at a chosen instant instead of by sleeping.
        """
        ...


def matches(expected: str, received: str) -> bool:
    """Constant-time comparison of two hex or base64 digests.

    `==` on strings returns as soon as two bytes differ, so the time it takes
    reveals how many leading characters were correct. Given enough attempts that
    is enough to reconstruct a valid signature one character at a time.
    `compare_digest` always examines everything.
    """
    return hmac.compare_digest(expected, received)


def truncated_body_detail(size: int) -> str:
    """The diagnosis nobody else can give, because nobody else truncated it.

    When Hooklab itself cut the body at the size limit, the HMAC cannot possibly
    match. Reporting that as "invalid signature" would send the developer hunting
    for a wrong secret that is in fact perfectly correct.
    """
    return (
        f"The body was truncated at {size} bytes by Hooklab's size limit, so the "
        "HMAC cannot match. This says nothing about your secret. Raise "
        "MAX_BODY_BYTES if you need to verify payloads this large."
    )
