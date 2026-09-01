"""Where captured requests get forwarded to."""

import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

VERIFICATION_TOKEN_BYTES = 16
DEFAULT_TIMEOUT_MS = 10_000
DEFAULT_MAX_ATTEMPTS = 8


def generate_verification_token() -> str:
    return secrets.token_urlsafe(VERIFICATION_TOKEN_BYTES)


class Destination(Base):
    """A URL that an endpoint's captures are forwarded to.

    **Nothing is forwarded until the destination is verified**, and that is an
    anti-abuse rule rather than a nicety. The SSRF checks stop someone aiming
    Hooklab at *our* network; they do nothing about someone aiming it at a
    stranger's website and using us as an amplifier. Requiring the destination to
    echo back a token proves whoever configured it controls that server.

    Verification is also what makes the blast radius of a stolen `view_token`
    survivable: an attacker who steals one still cannot point forwarding at a
    server they do not control.
    """

    __tablename__ = "destinations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )

    target_url: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Merged into each forwarded request. Useful for an API key the destination
    # expects; never used to override the signature headers, which have to arrive
    # exactly as the provider sent them.
    extra_headers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    timeout_ms: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_TIMEOUT_MS, server_default=str(DEFAULT_TIMEOUT_MS)
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_MAX_ATTEMPTS,
        server_default=str(DEFAULT_MAX_ATTEMPTS),
    )

    # The proof-of-ownership challenge. Sent to the destination, which has to
    # return it. Not a secret worth encrypting: it grants nothing, and it is
    # deliberately shown to the user so they can hard-code the response.
    verification_token: Mapped[str] = mapped_column(
        String(64), nullable=False, default=generate_verification_token
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Circuit breaker. A destination that keeps failing gets left alone for a
    # while instead of being hammered on every single capture -- which would turn
    # our retries into a denial of service against a site that is already down.
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None
