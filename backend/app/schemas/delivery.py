"""Schemas for forwarded deliveries."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DeliveryPublic(BaseModel):
    """One capture's journey to one destination."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    destination_id: uuid.UUID
    state: str = Field(description="pending, delivered, failed, or exhausted (the dead letters).")
    attempts: int
    next_attempt_at: datetime | None = Field(
        default=None, description="Null once the delivery is finished, one way or the other."
    )
    last_status_code: int | None
    last_error: str | None
    # Returned so the receiving side can be debugged: the developer can look for
    # this exact key in their own logs and see whether they processed it twice.
    idempotency_key: uuid.UUID
    created_at: datetime
    delivered_at: datetime | None
