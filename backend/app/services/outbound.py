"""The outbound HTTP client, pinned to an address that was already validated.

Ordinary HTTP clients resolve the hostname themselves, which quietly undoes the
SSRF check: between our lookup and theirs, DNS is free to answer differently and
point the second request at an internal address. That is DNS rebinding.

So the connection is opened to the IP that `app.security.ssrf` approved, while
the original hostname is still sent as the `Host` header and as the TLS SNI. The
destination sees the name it expects, virtual hosts keep working, and the
certificate is still verified against the hostname rather than the address --
verified against the address it would simply fail, which was checked before
relying on any of this.

Every failure comes back as a value rather than an exception. The caller is a
retry loop, and for it "the connection was refused" is an ordinary outcome to
record and schedule around, not an error to handle.
"""

import time
from dataclasses import dataclass, field

import httpx

from app.security.ssrf import SafeTarget

# Enough for a verification echo or an error message worth showing, and small
# enough that a hostile destination streaming forever cannot exhaust memory. The
# body of a delivery response is of no interest beyond diagnostics.
MAX_RESPONSE_BYTES = 64 * 1024

# Establishing a TCP connection should be quick even to the far side of the
# world. It is capped separately so that a destination which accepts connections
# and then stalls cannot consume the entire budget before a single byte is sent.
CONNECT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Attempt:
    """What happened on one try. Never an exception."""

    status_code: int | None
    body: str
    error: str | None
    elapsed_ms: int
    # Lower-cased, so callers never have to guess at capitalisation.
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        return self.status_code is not None


def _redirect_note(response: httpx.Response) -> str:
    """Explain a redirect, since it is refused rather than followed.

    Following one would mean connecting to a fresh host that never went through
    the address checks -- the classic way to walk an SSRF filter straight into
    the internal network. A webhook destination has no business redirecting.
    """
    location = response.headers.get("location", "(no Location header)")
    return (
        f"The destination answered {response.status_code} redirecting to {location}. "
        "Redirects are not followed: the new address would not have been checked. "
        "Point the destination at the final URL instead."
    )


async def send(
    target: SafeTarget,
    method: str,
    headers: dict[str, str],
    body: bytes,
    timeout_ms: int,
) -> Attempt:
    """Make one request to an already-validated target."""
    started = time.perf_counter()
    read_timeout = timeout_ms / 1000

    # The path and query come from the original URL; only the host part is
    # swapped for the address.
    _, _, remainder = target.url.partition("://")
    _, _, path = remainder.partition("/")
    bracketed = f"[{target.ip}]" if ":" in target.ip else target.ip
    url = f"{target.scheme}://{bracketed}:{target.port}/{path}"

    outgoing = {**headers, "Host": target.host_header}
    # Only meaningful over TLS, and harmless otherwise.
    extensions = {"sni_hostname": target.host}

    timeout = httpx.Timeout(read_timeout, connect=min(CONNECT_TIMEOUT_SECONDS, read_timeout))

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                method, url, headers=outgoing, content=body, extensions=extensions
            ) as response:
                if response.is_redirect:
                    return Attempt(response.status_code, "", _redirect_note(response), elapsed())

                # Read with a ceiling instead of `response.aread()`: a hostile
                # destination can answer with an endless stream, and a length
                # check after the fact arrives once the memory is already gone.
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk[: MAX_RESPONSE_BYTES - total])
                    total += len(chunk)
                    if total >= MAX_RESPONSE_BYTES:
                        break

                text = b"".join(chunks).decode("utf-8", errors="replace")
                lowered = {name.lower(): value for name, value in response.headers.items()}
                return Attempt(response.status_code, text, None, elapsed(), lowered)

    except httpx.TimeoutException:
        return Attempt(None, "", f"No answer within {timeout_ms} ms.", elapsed())
    except httpx.HTTPError as exc:
        return Attempt(None, "", f"{type(exc).__name__}: {exc}", elapsed())
