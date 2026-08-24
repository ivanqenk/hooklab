"""Captured HTTP request model."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Request(Base):
    """An HTTP request captured exactly as it arrived.

    About the body: it is ALWAYS stored raw (`body_raw`), and a parsed copy
    (`body_json`) is stored only when the content type justifies it and parsing
    succeeds. There are two separate reasons:

    1. Real webhooks send XML, form-encoded, multipart and binary, not just JSON.
    2. More importantly, a signature HMAC is computed over the EXACT bytes of the
       body. If only the parsed version were stored and later re-serialised,
       signature verification would fail even with the correct secret.
    """

    __tablename__ = "requests"

    # BigInteger autoincrement rather than a UUID, on purpose: being monotonic it
    # doubles as the SSE `Last-Event-ID`. The browser reconnects saying "I am at
    # 4711" and gets whatever it missed, with no cursor table involved.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False
    )

    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    query: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    headers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    content_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_raw: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    body_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    body_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    body_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    source_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        # The dominant query is "give me the last N requests for this endpoint".
        # A composite index starting with endpoint_id answers it with a range
        # scan; Postgres can walk it backwards for ORDER BY id DESC, so there is
        # no need to declare a direction.
        Index("ix_requests_endpoint_id_id", "endpoint_id", "id"),
    )
