"""Tests for the outbound HTTP client.

Against a real socket, not a mock: the whole point of this module is what it
does on the wire -- which address it dials, which Host it announces, what it
does when the answer never stops. A mock would agree with whatever we wrote.

The targets are built by hand rather than through `ssrf.validate`, because the
server under test is on 127.0.0.1 and validation exists precisely to refuse
that. Constructing the target directly keeps the SSRF rules honest while still
exercising the client.
"""

from app.security.ssrf import SafeTarget
from app.services.outbound import MAX_RESPONSE_BYTES, send
from tests.tiny_server import TinyServer, answers, echoes, stalls, streams_forever


def _target(port: int, host: str = "destination.example", path: str = "hooks") -> SafeTarget:
    return SafeTarget(
        url=f"http://{host}/{path}",
        scheme="http",
        host=host,
        port=port,
        ip="127.0.0.1",
    )


async def test_a_plain_request_gets_through() -> None:
    async with TinyServer(answers(200, b"thanks")) as server:
        attempt = await send(_target(server.port), "POST", {}, b'{"a":1}', 5_000)

    assert attempt.status_code == 200
    assert attempt.body == "thanks"
    assert attempt.error is None


async def test_the_destination_sees_the_hostname_not_the_address() -> None:
    """The whole trick of connecting by IP.

    The socket goes to the validated address, but the destination has to see the
    name it expects, or virtual hosts and TLS both break.
    """
    async with TinyServer(answers(200)) as server:
        await send(_target(server.port, host="webhooks.example"), "POST", {}, b"", 5_000)

    assert server.received[0].headers["host"] == f"webhooks.example:{server.port}"


async def test_the_path_and_body_survive_the_rewrite() -> None:
    async with TinyServer(answers(200)) as server:
        await send(
            _target(server.port, path="deep/path"), "PUT", {"x-custom": "v"}, b"payload", 5_000
        )

    request = server.received[0]
    assert request.method == "PUT"
    assert request.path == "/deep/path"
    assert request.headers["x-custom"] == "v"
    assert request.body == b"payload"


async def test_redirects_are_refused_not_followed() -> None:
    """Following one would connect to a host that never passed the checks.

    It is the classic way to walk an SSRF filter into the internal network: pass
    validation with a public URL, then redirect to 169.254.169.254.
    """
    async with TinyServer(answers(302, headers={"Location": "http://169.254.169.254/"})) as server:
        attempt = await send(_target(server.port), "POST", {}, b"", 5_000)

    assert attempt.status_code == 302
    assert attempt.error is not None
    assert "169.254.169.254" in attempt.error
    assert "not followed" in attempt.error


async def test_an_endless_response_is_cut_off() -> None:
    """A hostile destination can answer forever.

    Reading it all and checking the length afterwards is correct and useless: by
    then the memory is gone.
    """
    async with TinyServer(streams_forever()) as server:
        attempt = await send(_target(server.port), "POST", {}, b"", 5_000)

    assert attempt.status_code == 200
    assert len(attempt.body) <= MAX_RESPONSE_BYTES


async def test_a_destination_that_stalls_times_out() -> None:
    """It accepts the connection and then says nothing.

    Without a read timeout that request occupies a worker until the heat death of
    the universe, which is a denial of service against ourselves.
    """
    async with TinyServer(stalls()) as server:
        attempt = await send(_target(server.port), "POST", {}, b"", 700)

    assert attempt.status_code is None
    assert attempt.error is not None
    assert "No answer within 700 ms" in attempt.error


async def test_a_refused_connection_is_a_value_not_an_exception() -> None:
    """The caller is a retry loop; a dead destination is an ordinary outcome."""
    attempt = await send(_target(9), "POST", {}, b"", 2_000)

    assert attempt.status_code is None
    assert attempt.error is not None


async def test_the_challenge_header_reaches_the_destination() -> None:
    async with TinyServer(echoes("x-hooklab-verification")) as server:
        attempt = await send(
            _target(server.port), "POST", {"x-hooklab-verification": "tok123"}, b"{}", 5_000
        )

    assert attempt.body == "tok123"
