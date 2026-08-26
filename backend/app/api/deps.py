"""Shared route dependencies.

They are expressed as `Annotated` type aliases rather than `Depends()` default
values. Both work in FastAPI, but the annotated form keeps the dependency inside
the type, so a route signature reads as plain typed parameters, the alias is
reusable across routers, and it does not trip the general Python rule against
calling functions in argument defaults.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import SessionLocal, get_session
from app.core.redis import get_redis
from app.models import Endpoint

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
RedisDep = Annotated[Redis, Depends(get_redis)]


async def resolve_endpoint(view_token: str, session: SessionDep) -> Endpoint:
    """Resolve the read token into an endpoint, or refuse.

    Making this a dependency rather than a helper called from each route means
    the access check cannot be forgotten: a route that declares `EndpointDep`
    simply cannot run without it having passed.

    A missing endpoint answers 404 rather than 403, so nobody can tell a valid
    token apart from an invalid one -- which is exactly what someone spraying
    guesses is trying to learn.
    """
    result = await session.execute(select(Endpoint).where(Endpoint.view_token == view_token))
    endpoint = result.scalar_one_or_none()

    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    return endpoint


EndpointDep = Annotated[Endpoint, Depends(resolve_endpoint)]


async def resolve_endpoint_detached(view_token: str) -> Endpoint:
    """Same check as `resolve_endpoint`, but holding no session afterwards.

    `get_session` is a yield dependency, so its session stays open until the
    RESPONSE finishes -- and an SSE response never finishes. A stream route using
    `EndpointDep` would therefore hold a pooled connection for as long as the tab
    stays open; with the default pool of 5 plus 10 overflow, the sixteenth open
    tab exhausts the pool and every other route starts timing out.

    So this one opens its own session, closes it before returning, and hands back
    a detached instance. Reading its already-loaded attributes is safe; the stream
    only needs `id`.
    """
    async with SessionLocal() as session:
        result = await session.execute(select(Endpoint).where(Endpoint.view_token == view_token))
        endpoint = result.scalar_one_or_none()

    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")

    return endpoint


DetachedEndpointDep = Annotated[Endpoint, Depends(resolve_endpoint_detached)]
