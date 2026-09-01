"""Managing where an endpoint's captures get forwarded."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import EndpointDep, SessionDep
from app.models import Destination
from app.schemas.destination import DestinationCreate, DestinationPublic
from app.security.ssrf import UnsafeTarget, validate

router = APIRouter(prefix="/api/endpoints/{view_token}/destinations", tags=["destinations"])

MAX_DESTINATIONS = 10


def _public(destination: Destination) -> DestinationPublic:
    return DestinationPublic(
        id=destination.id,
        target_url=destination.target_url,
        active=destination.active,
        verified=destination.is_verified,
        verification_token=destination.verification_token,
        extra_headers=destination.extra_headers,
        timeout_ms=destination.timeout_ms,
        max_attempts=destination.max_attempts,
        consecutive_failures=destination.consecutive_failures,
        paused_until=destination.paused_until,
        created_at=destination.created_at,
    )


@router.post("", response_model=DestinationPublic, status_code=status.HTTP_201_CREATED)
async def create_destination(
    data: DestinationCreate,
    endpoint: EndpointDep,
    session: SessionDep,
) -> DestinationPublic:
    """Register a destination. It is created **unverified** and forwards nothing.

    The URL is checked here so a mistake is caught while the user is looking at
    the screen, but this check is convenience rather than the defence: DNS can
    change between now and the first delivery, so the worker validates again
    immediately before it connects. Only the second check is load-bearing.
    """
    try:
        validate(data.target_url)
    except UnsafeTarget as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    existing = await session.execute(
        select(Destination).where(Destination.endpoint_id == endpoint.id)
    )
    if len(list(existing.scalars().all())) >= MAX_DESTINATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An endpoint may have at most {MAX_DESTINATIONS} destinations.",
        )

    destination = Destination(
        endpoint_id=endpoint.id,
        target_url=data.target_url,
        extra_headers=data.extra_headers,
        timeout_ms=data.timeout_ms,
        max_attempts=data.max_attempts,
    )
    session.add(destination)
    await session.commit()
    await session.refresh(destination)

    return _public(destination)


@router.get("", response_model=list[DestinationPublic])
async def list_destinations(endpoint: EndpointDep, session: SessionDep) -> list[DestinationPublic]:
    result = await session.execute(
        select(Destination)
        .where(Destination.endpoint_id == endpoint.id)
        .order_by(Destination.created_at)
    )
    return [_public(row) for row in result.scalars().all()]


async def _load(
    session: SessionDep, endpoint: EndpointDep, destination_id: uuid.UUID
) -> Destination:
    """Fetch one destination, scoped to the endpoint the token unlocked."""
    result = await session.execute(
        select(Destination).where(
            Destination.id == destination_id,
            Destination.endpoint_id == endpoint.id,
        )
    )
    destination = result.scalar_one_or_none()

    if destination is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destination not found")

    return destination


@router.delete("/{destination_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_destination(
    destination_id: uuid.UUID,
    endpoint: EndpointDep,
    session: SessionDep,
) -> None:
    await session.delete(await _load(session, endpoint, destination_id))
    await session.commit()
