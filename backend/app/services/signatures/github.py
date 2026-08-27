"""GitHub webhook signatures.

The simplest of the schemes worth supporting: HMAC-SHA256 over the raw body,
hex encoded, in a single header with a `sha256=` prefix.

    X-Hub-Signature-256: sha256=7d38cdd689735b008b3c702edd92eea23791c5f6

GitHub also still sends the older `X-Hub-Signature` (HMAC-SHA1). It is not
implemented here on purpose: GitHub's own documentation tells you to ignore it,
and offering it would invite someone to verify against the weaker of the two.
"""

import hashlib
import hmac

from app.services.signatures.base import Reason, Verification, matches

PREFIX = "sha256="


class GitHubVerifier:
    name = "github"
    header = "x-hub-signature-256"

    def verify(self, body: bytes, headers: dict[str, str], secret: str, now: float) -> Verification:
        received = headers.get(self.header)

        if received is None:
            return Verification.failed(
                Reason.MISSING_HEADER,
                f"No {self.header} header arrived. GitHub only signs when the "
                "webhook has a secret configured -- check the webhook's settings "
                "in the repository or organisation.",
            )

        if not received.startswith(PREFIX):
            return Verification.failed(
                Reason.MALFORMED_HEADER,
                f"The header should look like '{PREFIX}<hex>' and it arrived as "
                f"'{received[:32]}'. If it starts with 'sha1=' you are reading "
                "X-Hub-Signature, the deprecated header.",
            )

        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        if not matches(expected, received.removeprefix(PREFIX)):
            return Verification.failed(
                Reason.MISMATCH,
                "The HMAC does not match. In order of likelihood: the secret "
                "belongs to a different webhook, it was copied with surrounding "
                "whitespace, or it was regenerated in GitHub without being "
                "updated here.",
            )

        return Verification.ok()
