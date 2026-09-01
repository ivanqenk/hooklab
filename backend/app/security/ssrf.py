"""Defence against server-side request forgery.

This is the most dangerous thing Hooklab does: a worker makes HTTP requests to a
URL the user chose, from inside our infrastructure. That request originates
behind whatever firewall we sit behind, so a destination of `http://127.0.0.1` or
`http://169.254.169.254` turns the forwarder into a window onto our own network
and our cloud metadata service.

Two ideas carry the whole module.

**Validate the resolved address, never the string.** `evil.com` is free to resolve
to `127.0.0.1`; a blocklist of hostnames stops nobody. The hostname is resolved to
every address it has, and the target is refused if *any* of them is unacceptable.

**Resolve once, then connect to that exact address.** Validating and then handing
the hostname to an HTTP client lets it resolve again, and DNS is free to answer
differently the second time -- first the innocuous address, then the internal one.
That is DNS rebinding, and it is the difference between a real mitigation and a
decorative one. `SafeTarget` therefore carries the IP to dial, with the original
hostname kept for the `Host` header and the TLS SNI.

The address rules were built against measurements rather than assumption, because
the standard library's own categories have holes:

- `is_private` misses carrier-grade NAT (`100.64.0.0/10`) and all multicast.
- `is_global` is **True** for multicast, and **True** for `64:ff9b::/96` -- the
  NAT64 prefix, which embeds an IPv4 address in its low 32 bits. On a network with
  NAT64, `64:ff9b::7f00:1` reaches `127.0.0.1`. Neither category catches it.

So IPv4 hidden inside IPv6 is unwrapped first, in all three of its forms, and
only then are the categories applied.
"""

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Ports for services that are normally only reachable from inside a network.
# Weaker than the address check -- anything can listen anywhere -- but it costs
# nothing and catches the careless cases.
BLOCKED_PORTS = frozenset({22, 23, 25, 135, 139, 445, 3306, 5432, 6379, 9200, 11211, 27017})

DEFAULT_PORTS = {"http": 80, "https": 443}

# IPv6 prefixes that carry an IPv4 address in their low 32 bits. Each is unwrapped
# and judged on the address inside, because that is where the packet ends up.
NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")
SIXTOFOUR_PREFIX = ipaddress.ip_network("2002::/16")


class UnsafeTarget(Exception):
    """The destination was refused, with a reason fit to show the user."""


@dataclass(frozen=True)
class SafeTarget:
    """A destination that passed every check, and how to reach it safely."""

    url: str
    scheme: str
    host: str
    port: int
    # The address to actually open the socket to. Connecting here rather than to
    # `host` is what closes the rebinding window: no second lookup happens.
    ip: str

    @property
    def host_header(self) -> str:
        """What the destination should see, regardless of which IP we dialled."""
        if self.port == DEFAULT_PORTS.get(self.scheme):
            return self.host
        return f"{self.host}:{self.port}"


def _unwrap(address: IPAddress) -> IPAddress:
    """Reduce an IPv6 address to the IPv4 address it actually carries, if any."""
    if not isinstance(address, ipaddress.IPv6Address):
        return address

    if address.ipv4_mapped is not None:
        return address.ipv4_mapped

    if address in NAT64_PREFIX:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)

    if address in SIXTOFOUR_PREFIX:
        # 2002:V4ADDR::/48 -- the IPv4 lives in the 32 bits after the prefix.
        return ipaddress.IPv4Address((int(address) >> 80) & 0xFFFFFFFF)

    return address


def rejection_reason(address: IPAddress) -> str | None:
    """Why this address may not be dialled, or None if it may.

    Default-deny: an address has to be globally routable unicast to pass, so a
    range nobody thought about is refused rather than allowed.
    """
    unwrapped = _unwrap(address)

    if unwrapped is not address:
        inner = rejection_reason(unwrapped)
        if inner is not None:
            return f"{address} carries {unwrapped}, which is {inner}"

    # Checked before is_global, which is True for multicast in both families.
    if unwrapped.is_multicast:
        return "a multicast address"

    if unwrapped.is_loopback:
        return "a loopback address"

    if unwrapped.is_link_local:
        # Includes 169.254.169.254, the cloud metadata service: the single most
        # valuable target of an SSRF, since it hands out credentials.
        return "link-local (this range holds the cloud metadata service)"

    if not unwrapped.is_global:
        # Covers private ranges, carrier-grade NAT, reserved, unspecified,
        # documentation and benchmarking space in one rule.
        return "not a globally routable address"

    return None


def _resolve(host: str, port: int) -> list[IPAddress]:
    """Every address the hostname has, both families."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeTarget(f"'{host}' could not be resolved: {exc.strerror}") from exc

    addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not addresses:
        raise UnsafeTarget(f"'{host}' resolved to no addresses")

    return addresses


def validate(url: str) -> SafeTarget:
    """Check a destination URL and return how to reach it, or refuse.

    Every resolved address has to pass. A hostname with one public address and one
    private one is refused outright: which one an HTTP client would pick is not
    something worth gambling on.
    """
    parts = urlsplit(url)

    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeTarget(
            f"Scheme '{parts.scheme or 'none'}' is not allowed; use http or https. "
            "Schemes like file:// and gopher:// exist to read local resources."
        )

    if not parts.hostname:
        raise UnsafeTarget("The URL has no host.")

    try:
        port = parts.port or DEFAULT_PORTS[parts.scheme]
    except ValueError as exc:
        raise UnsafeTarget("The port is not a number.") from exc

    if port in BLOCKED_PORTS:
        raise UnsafeTarget(f"Port {port} belongs to an internal service and is not allowed.")

    addresses = _resolve(parts.hostname, port)
    for address in addresses:
        reason = rejection_reason(address)
        if reason is not None:
            raise UnsafeTarget(
                f"'{parts.hostname}' resolves to {address}, which is {reason}. "
                "Destinations have to be reachable from the public internet."
            )

    return SafeTarget(
        url=url,
        scheme=parts.scheme,
        host=parts.hostname,
        port=port,
        # The first address is the one dialled, and it is the one that was
        # checked. Nothing resolves this hostname again.
        ip=str(addresses[0]),
    )
