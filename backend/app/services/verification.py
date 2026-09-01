"""Proving that whoever configured a destination controls it.

The SSRF rules stop Hooklab being aimed at *our* network. They do nothing about
it being aimed at a stranger's website: that address is perfectly public, and
forwarding to it would make us a free amplifier -- their server takes the load,
from our IP, and our IP takes the blame.

The fix is a challenge. Hooklab sends a token to the URL and the URL has to give
it back. Only someone who can change what that server returns can do that.
"""

from dataclasses import dataclass

from app.security.ssrf import SafeTarget
from app.services.outbound import send

CHALLENGE_HEADER = "x-hooklab-verification"

# The response has to be *exactly* the token, not merely contain it. That
# distinction is the whole strength of the check: a public request-echoing
# service -- httpbin and friends -- reflects the challenge header inside a JSON
# blob, so a "contains" rule would let anyone verify a destination they have no
# control over, and then point traffic at it.
#
# Answering with the bare token, or with the header set, is something only
# somebody editing that server can arrange.
MAX_ECHO_LENGTH = 200


@dataclass(frozen=True)
class VerificationOutcome:
    verified: bool
    detail: str


def _instructions(token: str) -> str:
    return (
        f"Have the destination answer 2xx with the body set to exactly '{token}', "
        f"or with the response header {CHALLENGE_HEADER} set to it, whenever a "
        f"request carries {CHALLENGE_HEADER}."
    )


async def verify_destination(
    target: SafeTarget, token: str, timeout_ms: int
) -> VerificationOutcome:
    """Challenge the destination and judge its answer.

    `target` has just been re-validated: the address is checked again immediately
    before connecting, because DNS may have changed since the destination was
    registered.
    """
    attempt = await send(
        target,
        method="POST",
        headers={CHALLENGE_HEADER: token, "content-type": "application/json"},
        body=b"{}",
        timeout_ms=timeout_ms,
    )

    if attempt.error is not None:
        return VerificationOutcome(False, attempt.error)

    if attempt.status_code is None or not (200 <= attempt.status_code < 300):
        return VerificationOutcome(
            False,
            f"The destination answered {attempt.status_code}. {_instructions(token)}",
        )

    echoed_header = attempt.headers.get(CHALLENGE_HEADER, "").strip()
    echoed_body = attempt.body.strip()

    if echoed_header == token or echoed_body == token:
        return VerificationOutcome(True, "The destination echoed the token. Forwarding is enabled.")

    if token in echoed_body:
        return VerificationOutcome(
            False,
            "The token appears inside the response but is not the whole of it. An exact "
            "answer is required, because a service that simply echoes requests back would "
            f"otherwise let anyone verify a destination they do not own. {_instructions(token)}",
        )

    seen = echoed_body[:MAX_ECHO_LENGTH] or "(empty body)"
    return VerificationOutcome(
        False, f"The destination answered 2xx but returned '{seen}'. {_instructions(token)}"
    )
