"""Capture endpoint model."""

import secrets
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 16 random bytes produce 22 base64url characters. Large enough that guessing a
# token is infeasible, short enough that the URL stays comfortable to paste into
# a provider's configuration panel.
TOKEN_BYTES = 16


def generate_token() -> str:
    """Cryptographically secure, URL-safe random token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


class Endpoint(Base):
    """A capture URL.

    It carries TWO distinct tokens on purpose, and that is a security decision:

    - `ingest_token` is PUBLIC: it travels in the URL pasted into Stripe's or
      GitHub's configuration, so it ends up in logs, screenshots and tickets.
    - `view_token` is SECRET: it is the only one that grants *read* access to the
      captured traffic.

    If they were the same, anyone who saw that URL in a configuration screen could
    read every captured payload -- which routinely include credentials and
    customer data.
    """

    __tablename__ = "endpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    ingest_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=generate_token
    )
    view_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=generate_token
    )

    # NULL means an anonymous endpoint. Provided for from the start even though
    # accounts arrive much later: adding the column afterwards would force a
    # decision about what value existing rows should take.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Signature verification. Both NULL means "do not verify"; they are always set
    # and cleared together.
    signature_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # ENCRYPTED at rest (AES-GCM, see app/core/crypto.py) and never returned by
    # the API in any form. Holding this secret lets an attacker FORGE webhooks the
    # customer's own systems will accept, which is worse than reading past
    # traffic. Text rather than bytea because the stored form is base64.
    signature_secret: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Anonymous endpoints expire; the retention worker deletes them once due.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
