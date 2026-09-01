"""Tests for proving ownership of a destination."""

import json

import pytest
from httpx import AsyncClient

from app.api import destinations as destinations_api
from app.security.ssrf import SafeTarget
from app.services.verification import CHALLENGE_HEADER, VerificationOutcome, verify_destination
from tests.tiny_server import Handler, TinyServer, answers, echoes, stalls

TOKEN = "challenge-token-abc"


def _target(port: int) -> SafeTarget:
    return SafeTarget(
        url="http://destination.example/hooks",
        scheme="http",
        host="destination.example",
        port=port,
        ip="127.0.0.1",
    )


async def _outcome(
    handler: Handler, token: str = TOKEN, timeout_ms: int = 3_000
) -> VerificationOutcome:
    async with TinyServer(handler) as server:
        return await verify_destination(_target(server.port), token, timeout_ms)


async def test_echoing_the_token_in_the_body_verifies() -> None:
    outcome = await _outcome(echoes(CHALLENGE_HEADER))

    assert outcome.verified
    assert "Forwarding is enabled" in outcome.detail


async def test_echoing_the_token_in_a_header_verifies() -> None:
    """Some frameworks make a header easier to set than an exact body."""
    outcome = await _outcome(answers(200, b"ok", {CHALLENGE_HEADER: TOKEN}))

    assert outcome.verified


async def test_a_service_that_merely_reflects_the_request_does_not_verify() -> None:
    """The reason the match has to be exact rather than a substring.

    Public request-echoing services reflect every header back inside a JSON blob.
    A "contains" rule would let anyone verify `https://some-echo-service/anything`
    -- a destination they have no control over -- and then aim traffic at it.
    """
    reflected = json.dumps({"headers": {CHALLENGE_HEADER: TOKEN}, "url": "/anything"}).encode()

    outcome = await _outcome(answers(200, reflected))

    assert not outcome.verified
    assert "not the whole of it" in outcome.detail


async def test_a_2xx_with_the_wrong_body_does_not_verify() -> None:
    outcome = await _outcome(answers(200, b"hello world"))

    assert not outcome.verified
    assert "hello world" in outcome.detail
    assert TOKEN in outcome.detail, "the instructions have to name the token"


async def test_an_empty_answer_does_not_verify() -> None:
    outcome = await _outcome(answers(200, b""))

    assert not outcome.verified
    assert "(empty body)" in outcome.detail


@pytest.mark.parametrize("status", [301, 400, 401, 404, 500, 503])
async def test_a_non_2xx_answer_does_not_verify(status: int) -> None:
    outcome = await _outcome(answers(status, TOKEN.encode()))

    assert not outcome.verified
    assert str(status) in outcome.detail


async def test_a_destination_that_never_answers_does_not_verify() -> None:
    outcome = await _outcome(stalls(), timeout_ms=600)

    assert not outcome.verified
    assert "600 ms" in outcome.detail


# --- Through the API -------------------------------------------------------


async def _register(client: AsyncClient) -> tuple[dict[str, str], dict[str, str]]:
    endpoint = dict((await client.post("/api/endpoints", json={})).json())
    created = dict(
        (
            await client.post(
                f"/api/endpoints/{endpoint['view_token']}/destinations",
                json={"target_url": "https://example.com/webhook"},
            )
        ).json()
    )
    return endpoint, created


def _pin(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    """Point validation at the local test server.

    The SSRF rules exist to refuse 127.0.0.1 and they are covered by their own
    suite; overriding them here is what lets the API flow be tested at all.
    """
    monkeypatch.setattr(destinations_api, "validate", lambda url: _target(port))


async def test_verifying_through_the_api_enables_forwarding(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint, created = await _register(client)

    async with TinyServer(echoes(CHALLENGE_HEADER)) as server:
        _pin(monkeypatch, server.port)
        response = await client.post(
            f"/api/endpoints/{endpoint['view_token']}/destinations/{created['id']}/verify"
        )

    assert response.status_code == 200
    assert response.json()["verified"] is True

    listed = (await client.get(f"/api/endpoints/{endpoint['view_token']}/destinations")).json()
    assert listed[0]["verified"] is True


async def test_a_failed_verification_is_explained_not_an_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 with `verified: false`, not a 4xx.

    The request was perfectly fine; the destination simply is not ready yet, and
    the user needs to read why rather than see a status code.
    """
    endpoint, created = await _register(client)

    async with TinyServer(answers(200, b"nope")) as server:
        _pin(monkeypatch, server.port)
        response = await client.post(
            f"/api/endpoints/{endpoint['view_token']}/destinations/{created['id']}/verify"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is False
    assert created["verification_token"] in body["detail"]

    listed = (await client.get(f"/api/endpoints/{endpoint['view_token']}/destinations")).json()
    assert listed[0]["verified"] is False


async def test_the_address_is_revalidated_at_verification_time(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registration-time validation is not enough on its own.

    DNS can change between registering a destination and using it -- innocently,
    or because someone was waiting for exactly that. The check that counts is the
    one immediately before connecting.
    """
    from app.security.ssrf import UnsafeTarget

    endpoint, created = await _register(client)

    def now_dangerous(url: str) -> SafeTarget:
        raise UnsafeTarget("resolves to 127.0.0.1, which is a loopback address")

    monkeypatch.setattr(destinations_api, "validate", now_dangerous)

    response = await client.post(
        f"/api/endpoints/{endpoint['view_token']}/destinations/{created['id']}/verify"
    )

    assert response.json()["verified"] is False
    assert "loopback" in response.json()["detail"]
