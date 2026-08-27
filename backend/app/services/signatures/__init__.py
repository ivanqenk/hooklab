"""Signature verification, dispatched by provider.

Only this module is imported from the rest of the app; the individual providers
stay behind it, so adding Shopify or Twilio later touches nothing else.
"""

from app.services.signatures.base import Reason, Verification, Verifier, truncated_body_detail
from app.services.signatures.github import GitHubVerifier
from app.services.signatures.stripe import StripeVerifier

VERIFIERS: dict[str, Verifier] = {
    verifier.name: verifier for verifier in (StripeVerifier(), GitHubVerifier())
}

PROVIDERS = tuple(sorted(VERIFIERS))


def verify(
    provider: str,
    secret: str,
    body: bytes,
    headers: dict[str, str],
    body_truncated: bool,
    body_size: int,
    now: float,
) -> Verification:
    """Check one capture against the endpoint's configured secret.

    Raises `KeyError` for an unknown provider. That is deliberate: the provider
    name is validated when it is configured, so reaching here with a bad one is a
    bug in our code, not bad input, and it should be loud rather than silently
    reported as a failed signature.
    """
    verifier = VERIFIERS[provider]

    if not secret:
        return Verification.failed(
            Reason.NO_SECRET,
            f"No secret is configured for {provider}, so nothing can be checked. "
            "Set one on the endpoint to start verifying.",
        )

    # Checked here rather than inside each provider: a body Hooklab itself cut
    # short can never produce a matching HMAC, and that is true of every scheme.
    if body_truncated:
        return Verification.failed(Reason.BODY_TRUNCATED, truncated_body_detail(body_size))

    return verifier.verify(body, headers, secret, now)


__all__ = ["PROVIDERS", "VERIFIERS", "Reason", "Verification", "Verifier", "verify"]
