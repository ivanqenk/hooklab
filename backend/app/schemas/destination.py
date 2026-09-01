"""Schemas for forwarding destinations."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.destination import DEFAULT_MAX_ATTEMPTS, DEFAULT_TIMEOUT_MS


class DestinationCreate(BaseModel):
    """Register a URL to forward captures to."""

    target_url: str = Field(
        max_length=2048,
        description="Where to forward. Must be http or https and publicly reachable.",
    )
    extra_headers: dict[str, str] | None = Field(
        default=None,
        description="Merged into each forwarded request, for an API key the destination expects.",
    )
    timeout_ms: int = Field(default=DEFAULT_TIMEOUT_MS, ge=1_000, le=30_000)
    max_attempts: int = Field(default=DEFAULT_MAX_ATTEMPTS, ge=1, le=20)


class DestinationPublic(BaseModel):
    """A registered destination and how far along its verification is."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_url: str
    active: bool
    verified: bool = Field(description="Nothing is forwarded until this is true.")
    # Deliberately returned. It is a challenge, not a credential: it grants
    # nothing, and the user cannot configure their server to echo it back without
    # being able to read it.
    verification_token: str
    extra_headers: dict[str, str] | None
    timeout_ms: int
    max_attempts: int
    consecutive_failures: int
    paused_until: datetime | None
    created_at: datetime


class VerificationResult(BaseModel):
    """The outcome of a verification attempt, with a reason when it failed."""

    verified: bool
    detail: str
