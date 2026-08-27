"""Result of verifying one capture's signature."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SignatureCheck(Base):
    """What happened when a capture was checked against the endpoint's secret.

    A separate table rather than columns on `requests`, for two reasons. Most
    captures have no check at all -- verification only happens once a provider and
    secret are configured -- and re-checking a whole endpoint after the user fixes
    a wrong secret becomes a single upsert instead of a wide update over a table
    that also holds the bodies.

    `request_id` is the primary key, so a capture can only ever hold one current
    result: a re-check replaces the old verdict rather than accumulating history
    nobody asked for.
    """

    __tablename__ = "signature_checks"

    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), primary_key=True
    )

    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # The machine-readable case (`mismatch`, `timestamp_out_of_tolerance`...) and
    # the sentence a human reads. Stored rather than recomputed because the
    # diagnosis depends on the secret in force at the time, and that may have
    # changed by the time anyone reads it.
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)

    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
