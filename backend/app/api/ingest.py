"""Webhook ingestion: capture whatever arrives, however it arrives."""

import time
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select, update

from app.api.deps import RedisDep, SessionDep, SettingsDep
from app.models import CapturedRequest, Endpoint
from app.schemas.request import RequestSummary
from app.services.bus import publish
from app.services.capture import (
    normalise_headers,
    normalise_query,
    parse_json_body,
    read_body_with_limit,
)

# No prefix: the ingest URL is pasted into third-party configuration panels, so
# every character counts. It is also excluded from the OpenAPI schema -- seven
# methods across two paths would bury the actual management API under noise.
router = APIRouter(tags=["ingest"])

ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


async def _capture(
    token: str,
    path: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
) -> Response:
    started = time.perf_counter()

    result = await session.execute(select(Endpoint).where(Endpoint.ingest_token == token))
    endpoint = result.scalar_one_or_none()

    if endpoint is None or endpoint.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown ingest endpoint")

    body, truncated = await read_body_with_limit(request, settings.max_body_bytes)
    content_type = request.headers.get("content-type")

    captured = CapturedRequest(
        endpoint_id=endpoint.id,
        method=request.method,
        path=f"/{path}" if path else "",
        query=normalise_query(request),
        headers=normalise_headers(request),
        content_type=content_type,
        body_raw=body or None,
        body_json=parse_json_body(body, content_type, truncated),
        body_size=len(body),
        body_truncated=truncated,
        # request.client is the immediate peer. Behind a reverse proxy that is
        # the proxy itself; the fix is running uvicorn with --proxy-headers so
        # the ASGI layer resolves X-Forwarded-For from trusted hops, NOT parsing
        # that header here, which any client can forge.
        source_ip=request.client.host if request.client else None,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    session.add(captured)

    # Incremented in SQL rather than read-modify-write in Python. With two
    # webhooks arriving at once, `endpoint.request_count += 1` on both sides
    # would read the same value and one increment would silently vanish.
    await session.execute(
        update(Endpoint)
        .where(Endpoint.id == endpoint.id)
        .values(request_count=Endpoint.request_count + 1)
    )

    # flush assigns the id without ending the transaction, so it can go in the
    # response body.
    await session.flush()
    request_id = captured.id
    await session.commit()

    # Announced AFTER the commit, never before. Publishing first would show the
    # browser a capture that a failed transaction then rolled back -- and a reader
    # reconnecting later would refill from Postgres and never find it again. The
    # summary is what the live list renders; the body is fetched on click.
    await publish(
        redis, endpoint.id, RequestSummary.model_validate(captured).model_dump(mode="json")
    )

    # Always 200, even for a truncated body. Providers retry on any non-2xx, and
    # a retry storm would bury the very payload the developer is trying to read.
    # The truncation is reported in the stored record instead.
    return Response(
        content=f'{{"received":true,"request_id":{request_id}}}',
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )


@router.api_route("/in/{token}", methods=ALL_METHODS, include_in_schema=False)
async def ingest_root(
    token: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
) -> Response:
    """Capture a request sent to the bare ingest URL."""
    return await _capture(token, "", request, session, settings, redis)


@router.api_route("/in/{token}/{path:path}", methods=ALL_METHODS, include_in_schema=False)
async def ingest_subpath(
    token: str,
    path: str,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    redis: RedisDep,
) -> Response:
    """Capture a request sent to any sub-path of the ingest URL.

    Providers often append their own segments, and a developer may deliberately
    use different paths to tell event kinds apart. The path is recorded rather
    than being a reason to reject.
    """
    return await _capture(token, path, request, session, settings, redis)
