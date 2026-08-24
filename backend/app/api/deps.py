"""Shared route dependencies.

They are expressed as `Annotated` type aliases rather than `Depends()` default
values. Both work in FastAPI, but the annotated form keeps the dependency inside
the type, so a route signature reads as plain typed parameters, the alias is
reusable across routers, and it does not trip the general Python rule against
calling functions in argument defaults.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session
from app.models import Endpoint

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


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
