"""End-to-end tests for signature verification through the API."""

import hashlib
import hmac
import json

from httpx import AsyncClient

from app.main import app
from tests.sse import SSEStream

SECRET = "It's a Secret to Everybody"
BODY = b'{"action":"opened","number":1}'


def _github_signature(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _endpoint(client: AsyncClient, provider: str | None = "github") -> dict[str, str]:
    endpoint = dict((await client.post("/api/endpoints", json={})).json())
    if provider is not None:
        response = await client.put(
            f"/api/endpoints/{endpoint['view_token']}/signature",
            json={"provider": provider, "secret": SECRET},
        )
        assert response.status_code == 200
    return endpoint


async def _send(client: AsyncClient, endpoint: dict[str, str], body: bytes, **headers: str) -> None:
    await client.post(f"/in/{endpoint['ingest_token']}", content=body, headers=headers)


async def _latest(client: AsyncClient, endpoint: dict[str, str]) -> dict[str, object]:
    listed = (await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")).json()
    request_id = listed["items"][0]["id"]
    detail: dict[str, object] = (
        await client.get(f"/api/endpoints/{endpoint['view_token']}/requests/{request_id}")
    ).json()
    return detail


# --- Configuration ---------------------------------------------------------


async def test_configuring_a_signature_reports_the_provider(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)

    body = (await client.get(f"/api/endpoints/{endpoint['view_token']}")).json()

    assert body["signature_provider"] == "github"


async def test_the_secret_is_never_returned_by_any_route(client: AsyncClient) -> None:
    """The single most important assertion in this module.

    Holding this secret lets an attacker forge webhooks that the customer's own
    systems accept as genuine. It is write-only: not masked, absent.
    """
    endpoint = await _endpoint(client)
    await _send(client, endpoint, BODY, **{"x-hub-signature-256": _github_signature(BODY)})

    responses = [
        await client.put(
            f"/api/endpoints/{endpoint['view_token']}/signature",
            json={"provider": "github", "secret": SECRET},
        ),
        await client.get(f"/api/endpoints/{endpoint['view_token']}"),
        await client.get(f"/api/endpoints/{endpoint['view_token']}/requests"),
    ]
    detail = await _latest(client, endpoint)

    for response in responses:
        assert SECRET not in response.text
        assert "signature_secret" not in response.text
    assert SECRET not in json.dumps(detail)


async def test_an_unknown_provider_is_rejected(client: AsyncClient) -> None:
    endpoint = await _endpoint(client, provider=None)

    response = await client.put(
        f"/api/endpoints/{endpoint['view_token']}/signature",
        json={"provider": "paypal", "secret": SECRET},
    )

    assert response.status_code == 422
    assert "github" in response.json()["detail"]


async def test_the_public_ingest_token_cannot_configure_verification(client: AsyncClient) -> None:
    """The ingest token ends up in configuration panels, logs and screenshots.

    If it could set the signing secret, anyone who saw the URL could turn
    verification off or point it at a secret of their own.
    """
    endpoint = await _endpoint(client, provider=None)

    response = await client.put(
        f"/api/endpoints/{endpoint['ingest_token']}/signature",
        json={"provider": "github", "secret": SECRET},
    )

    assert response.status_code == 404


async def test_clearing_stops_verification(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)

    assert (
        await client.delete(f"/api/endpoints/{endpoint['view_token']}/signature")
    ).status_code == 204
    await _send(client, endpoint, BODY, **{"x-hub-signature-256": _github_signature(BODY)})

    assert (await _latest(client, endpoint))["signature"] is None


# --- Verification on the way in --------------------------------------------


async def test_a_correctly_signed_webhook_is_marked_valid(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)

    await _send(client, endpoint, BODY, **{"x-hub-signature-256": _github_signature(BODY)})

    signature = (await _latest(client, endpoint))["signature"]
    assert isinstance(signature, dict)
    assert signature["valid"] is True
    assert signature["provider"] == "github"
    assert signature["reason"] == "valid"


async def test_a_wrong_secret_is_diagnosed_not_just_rejected(client: AsyncClient) -> None:
    """The reason this feature exists: the verdict explains what to do next."""
    endpoint = await _endpoint(client)

    await _send(client, endpoint, BODY, **{"x-hub-signature-256": _github_signature(BODY, "wrong")})

    signature = (await _latest(client, endpoint))["signature"]
    assert isinstance(signature, dict)
    assert signature["valid"] is False
    assert signature["reason"] == "mismatch"
    assert "different webhook" in signature["detail"]


async def test_an_unsigned_webhook_says_the_header_is_missing(client: AsyncClient) -> None:
    endpoint = await _endpoint(client)

    await _send(client, endpoint, BODY)

    signature = (await _latest(client, endpoint))["signature"]
    assert isinstance(signature, dict)
    assert signature["reason"] == "missing_header"


async def test_a_capture_still_succeeds_when_the_signature_fails(client: AsyncClient) -> None:
    """A bad signature must never cost the developer the payload.

    Refusing the capture would hide the very request they need in order to work
    out why verification is failing.
    """
    endpoint = await _endpoint(client)

    response = await client.post(
        f"/in/{endpoint['ingest_token']}",
        content=BODY,
        headers={"x-hub-signature-256": "sha256=00"},
    )

    assert response.status_code == 200
    assert (await _latest(client, endpoint))["body_text"] == BODY.decode()


async def test_endpoints_without_a_provider_are_not_checked(client: AsyncClient) -> None:
    endpoint = await _endpoint(client, provider=None)

    await _send(client, endpoint, BODY, **{"x-hub-signature-256": _github_signature(BODY)})

    assert (await _latest(client, endpoint))["signature"] is None


async def test_the_verdict_is_computed_over_the_exact_bytes(client: AsyncClient) -> None:
    """Byte-for-byte, which is what storing the raw body was for.

    This payload has the significant whitespace that a parse-and-reserialise
    round trip would silently normalise away, breaking the HMAC.
    """
    spaced = b'{ "action" :  "opened" ,  "number" : 1 }'
    endpoint = await _endpoint(client)

    await _send(client, endpoint, spaced, **{"x-hub-signature-256": _github_signature(spaced)})

    signature = (await _latest(client, endpoint))["signature"]
    assert isinstance(signature, dict)
    assert signature["valid"] is True


# --- How the verdict travels -----------------------------------------------


async def test_the_listing_carries_a_badge_without_the_explanation(client: AsyncClient) -> None:
    """Enough to draw an icon per row, without a paragraph on each one."""
    endpoint = await _endpoint(client)
    await _send(client, endpoint, BODY, **{"x-hub-signature-256": _github_signature(BODY)})

    item = (await client.get(f"/api/endpoints/{endpoint['view_token']}/requests")).json()["items"][
        0
    ]

    assert item["signature"]["valid"] is True
    assert "detail" not in item["signature"]


async def test_the_live_feed_carries_the_verdict(client: AsyncClient) -> None:
    """The badge appears with the capture, not a moment later.

    Verification runs inline during ingest precisely so the browser never shows a
    row whose verdict is still pending.
    """
    endpoint = await _endpoint(client)

    async with SSEStream(app, f"/api/endpoints/{endpoint['view_token']}/stream") as sse:
        assert (await sse.next_frame())["event"] == "ready"
        await _send(client, endpoint, BODY, **{"x-hub-signature-256": _github_signature(BODY)})
        payload = json.loads((await sse.next_frame())["data"])

    assert payload["signature"]["valid"] is True
    assert payload["signature"]["reason"] == "valid"
