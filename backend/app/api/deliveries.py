"""Reading and retrying forwarded deliveries."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import EndpointDep, SessionDep
from app.models import Delivery, DeliveryState, Destination
from app.schemas.delivery import DeliveryPublic

router = APIRouter(prefix="/api/endpoints/{view_token}/deliveries", tags=["deliveries"])

MAX_PAGE_SIZE = 100

# Annotated aliases rather than `Query(...)` in a default value. Both work, but a
# call in an argument default is a general Python hazard and ruff flags it; the
# annotated form keeps the parameter reading as a plain typed argument. It also
# sidesteps a wrinkle in the linter, which recognises the pattern for built-in
# types but not for our own enum.
StateFilter = Annotated[
    DeliveryState | None,
    Query(description="Filter by state; `exhausted` is the dead-letter queue."),
]
PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


@router.get("", response_model=list[DeliveryPublic])
async def list_deliveries(
    endpoint: EndpointDep,
    session: SessionDep,
    state: StateFilter = None,
    limit: PageLimit = 50,
) -> list[DeliveryPublic]:
    """Deliveries for this endpoint, newest first.

    Scoped through the destination, which is what keeps one token from reading
    another endpoint's delivery history.
    """
    statement = (
        select(Delivery)
        .join(Destination, Destination.id == Delivery.destination_id)
        .where(Destination.endpoint_id == endpoint.id)
        .order_by(Delivery.id.desc())
        .limit(limit)
    )
    if state is not None:
        statement = statement.where(Delivery.state == state)

    return [
        DeliveryPublic.model_validate(row) for row in (await session.execute(statement)).scalars()
    ]


@router.post("/{delivery_id}/retry", response_model=DeliveryPublic)
async def retry(
    delivery_id: int,
    endpoint: EndpointDep,
    session: SessionDep,
) -> DeliveryPublic:
    """Put an exhausted delivery back in the queue.

    The attempt count is reset rather than continued. A user clicking retry has
    just fixed something on their side, and leaving the counter at its maximum
    would give the fix a single attempt before giving up again -- which reads as
    the button not working.

    The destination's circuit breaker is released for the same reason: the pause
    exists to protect a destination that was failing, and the user is asserting
    that it no longer is.
    """
    result = await session.execute(
        select(Delivery, Destination)
        .join(Destination, Destination.id == Delivery.destination_id)
        .where(Delivery.id == delivery_id, Destination.endpoint_id == endpoint.id)
    )
    row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found")

    delivery, destination = row

    if delivery.state == DeliveryState.DELIVERED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This delivery already succeeded. Retrying would send a duplicate.",
        )

    delivery.state = DeliveryState.PENDING
    delivery.attempts = 0
    delivery.last_error = None
    delivery.next_attempt_at = datetime.now(UTC)

    destination.consecutive_failures = 0
    destination.paused_until = None

    session.add_all([delivery, destination])
    await session.commit()
    await session.refresh(delivery)

    return DeliveryPublic.model_validate(delivery)


@router.get("/by-request/{request_id}", response_model=list[DeliveryPublic])
async def deliveries_for_request(
    request_id: int,
    endpoint: EndpointDep,
    session: SessionDep,
) -> list[DeliveryPublic]:
    """Every destination's outcome for one capture, for the detail view."""
    statement = (
        select(Delivery)
        .join(Destination, Destination.id == Delivery.destination_id)
        .where(Delivery.request_id == request_id, Destination.endpoint_id == endpoint.id)
        .order_by(Delivery.id)
    )
    return [
        DeliveryPublic.model_validate(row) for row in (await session.execute(statement)).scalars()
    ]
