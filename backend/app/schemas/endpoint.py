"""Request and response schemas for capture endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.services.signatures import PROVIDERS


class SignatureConfigure(BaseModel):
    """Turn on verification for an endpoint.

    There is no matching response carrying the secret back, and there never will
    be: this is write-only. Holding it lets an attacker forge webhooks the
    customer's own systems accept as genuine.
    """

    provider: str = Field(description=f"One of: {', '.join(PROVIDERS)}.")
    secret: str = Field(
        min_length=1,
        max_length=512,
        description="The signing secret from the provider's dashboard.",
    )


class EndpointCreate(BaseModel):
    """Request body for creating an endpoint. Everything is optional."""

    name: str | None = Field(
        default=None,
        max_length=120,
        description="Label to recognise it by, for example 'Stripe webhooks'.",
    )


class EndpointCreated(BaseModel):
    """Response returned when an endpoint is created.

    This is the **only** place `view_token` is ever returned. That token is the
    read key: whoever holds it can see all captured traffic. Since it is not
    retrievable anywhere else, losing it means losing access -- much like a
    one-time link.

    That is deliberate. The alternative -- being able to recover it later --
    would require some form of identity, and in this phase endpoints are
    anonymous by design.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    ingest_url: str = Field(description="URL ready to paste into the provider.")
    ingest_token: str
    view_token: str
    created_at: datetime
    expires_at: datetime


class EndpointPublic(BaseModel):
    """View of an existing endpoint.

    It NEVER includes `view_token`. Whoever is reading already has it -- they used
    it to get here -- and repeating it in every response would only multiply the
    places it can leak from: browser history, proxy logs, screenshots.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str | None
    ingest_url: str
    created_at: datetime
    expires_at: datetime
    request_count: int
    # Which provider is being verified, if any. The secret itself is absent --
    # not masked, absent. A masked value still leaks its length and would invite
    # someone to "just show a few characters" later.
    signature_provider: str | None = None
