"""The SSRF table: every one of these must be refused.

These are plain functions with no database and no network -- DNS is stubbed, so
the suite neither depends on the internet nor accidentally dials anything.
"""

import ipaddress
import socket

import pytest

from app.security import ssrf
from app.security.ssrf import UnsafeTarget

# Every case here has been used to reach something it should not have.
BLOCKED = [
    ("127.0.0.1", "loopback"),
    ("0.0.0.0", "the unspecified address, which many stacks route to localhost"),
    ("10.0.0.1", "private, class A"),
    ("172.16.0.1", "private, the range most often left out of hand-written lists"),
    ("192.168.1.1", "private, class C"),
    ("169.254.169.254", "the cloud metadata service, the crown jewel of any SSRF"),
    ("100.64.0.1", "carrier-grade NAT, which `is_private` reports as False"),
    ("198.18.0.1", "benchmarking range"),
    ("224.0.0.1", "IPv4 multicast, which `is_global` reports as True"),
    ("240.0.0.1", "reserved"),
    ("255.255.255.255", "broadcast"),
    ("::1", "IPv6 loopback"),
    ("::", "IPv6 unspecified"),
    ("fc00::1", "IPv6 unique local"),
    ("fe80::1", "IPv6 link-local"),
    ("ff02::1", "IPv6 multicast, which `is_global` reports as True"),
    ("::ffff:127.0.0.1", "IPv4-mapped loopback, the bypass everyone forgets"),
    ("::ffff:169.254.169.254", "IPv4-mapped metadata service"),
    ("64:ff9b::7f00:1", "NAT64 carrying 127.0.0.1; both stdlib categories allow it"),
    ("2002:7f00:1::", "6to4 carrying 127.0.0.1"),
]

ALLOWED = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"]


@pytest.mark.parametrize(("address", "why"), BLOCKED, ids=[a for a, _ in BLOCKED])
def test_dangerous_addresses_are_refused(address: str, why: str) -> None:
    reason = ssrf.rejection_reason(ipaddress.ip_address(address))

    assert reason is not None, f"{address} must be refused: it is {why}"


@pytest.mark.parametrize("address", ALLOWED)
def test_public_addresses_are_allowed(address: str) -> None:
    """The rules have to be a filter, not a wall."""
    assert ssrf.rejection_reason(ipaddress.ip_address(address)) is None


def test_the_embedded_address_is_named_in_the_reason() -> None:
    """A refusal has to explain itself, or it looks like a bug to the user."""
    reason = ssrf.rejection_reason(ipaddress.ip_address("64:ff9b::7f00:1"))

    assert reason is not None
    assert "127.0.0.1" in reason


# --- Whole URLs ------------------------------------------------------------


def _stub_dns(monkeypatch: pytest.MonkeyPatch, *addresses: str) -> None:
    """Answer every lookup with these addresses, so tests never touch the network."""

    def fake(host: str, port: int, *args: object, **kwargs: object) -> list[tuple[object, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))
            for address in addresses
        ]

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fake)


def test_a_public_destination_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_dns(monkeypatch, "93.184.216.34")

    target = ssrf.validate("https://example.com/hooks")

    assert target.ip == "93.184.216.34"
    assert target.host == "example.com"
    assert target.port == 443


def test_a_hostname_resolving_to_a_private_address_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason a blocklist of hostnames is worthless.

    Nothing stops an attacker from pointing their own domain at 127.0.0.1, so the
    resolved address is what has to be judged.
    """
    _stub_dns(monkeypatch, "127.0.0.1")

    with pytest.raises(UnsafeTarget, match="loopback"):
        ssrf.validate("https://totally-innocent.example/hooks")


def test_one_bad_address_out_of_several_refuses_the_whole_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host with a public and a private address is not a coin worth flipping.

    Which one an HTTP client picks is up to the resolver and the OS, so a
    destination that has any unacceptable address is refused outright.
    """
    _stub_dns(monkeypatch, "93.184.216.34", "10.0.0.5")

    with pytest.raises(UnsafeTarget, match="10.0.0.5"):
        ssrf.validate("https://mixed.example/hooks")


def test_the_validated_address_is_the_one_to_dial(monkeypatch: pytest.MonkeyPatch) -> None:
    """The defence against DNS rebinding.

    Validating and then handing the hostname to an HTTP client lets it resolve a
    second time -- and the second answer can be the internal address. The target
    carries the IP so no second lookup ever happens.
    """
    _stub_dns(monkeypatch, "93.184.216.34")

    target = ssrf.validate("https://example.com:8443/hooks")

    assert target.ip == "93.184.216.34"
    # The destination still sees the name it expects, so TLS and vhosts work.
    assert target.host_header == "example.com:8443"


def test_the_default_port_is_left_out_of_the_host_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dns(monkeypatch, "93.184.216.34")

    assert ssrf.validate("https://example.com/x").host_header == "example.com"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/",
        "redis://example.com/",
        "//example.com/no-scheme",
    ],
)
def test_only_http_and_https_are_allowed(url: str) -> None:
    """The other schemes exist to read local resources, which is the whole attack."""
    with pytest.raises(UnsafeTarget, match="Scheme"):
        ssrf.validate(url)


@pytest.mark.parametrize("port", [22, 25, 3306, 5432, 6379, 11211, 27017])
def test_internal_service_ports_are_refused(port: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Defence in depth, not the main defence.

    Anything can listen on any port, so this does not replace the address check;
    it just costs nothing and catches the careless cases.
    """
    _stub_dns(monkeypatch, "93.184.216.34")

    with pytest.raises(UnsafeTarget, match="internal service"):
        ssrf.validate(f"http://example.com:{port}/")


@pytest.mark.parametrize(
    "host",
    ["127.1", "2130706433", "0177.0.0.1", "0x7f000001", "[::1]", "0"],
    ids=["short", "decimal", "octal", "hex", "bracketed-v6", "zero"],
)
def test_obfuscated_forms_of_localhost_are_refused(host: str) -> None:
    """The classic SSRF bypass list, and the reason strings are never trusted.

    None of these parse as an IP address, so a filter that pattern-matched the
    URL would wave them all through -- but the C library happily turns every one
    of them into 127.0.0.1 or 0.0.0.0. Judging the *resolved* address is what
    covers them, including forms nobody has thought of yet.

    Real resolution on purpose, not a stub: the point is exactly what the system
    resolver does with these. No network is involved -- they are numeric.
    """
    with pytest.raises(UnsafeTarget):
        ssrf.validate(f"http://{host}/hooks")


def test_an_octal_looking_host_that_is_genuinely_public_is_allowed() -> None:
    """The counterweight: `010.0.0.1` really is 8.0.0.1, and that is public.

    Refusing everything that merely looks odd would make the filter a wall.
    """
    assert ssrf.validate("http://010.0.0.1/hooks").ip == "8.0.0.1"


def test_a_url_with_no_host_is_refused() -> None:
    with pytest.raises(UnsafeTarget, match="no host"):
        ssrf.validate("http:///just-a-path")


def test_a_name_that_does_not_resolve_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> list[tuple[object, ...]]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(ssrf.socket, "getaddrinfo", fail)

    with pytest.raises(UnsafeTarget, match="could not be resolved"):
        ssrf.validate("https://nope.invalid/")
