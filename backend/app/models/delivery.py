"""One attempt-tracked forwarding of a capture to a destination."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DeliveryState(enum.StrEnum):
    """Where a delivery is in its life.

    `EXHAUSTED` is the dead-letter state: every attempt was used and none worked.
    It is kept separate from `FAILED` -- which is transient and will be retried --
    because the two need completely different reactions from a human.
    """

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    EXHAUSTED = "exhausted"


class Delivery(Base):
    """The forwarding of one capture to one destination.

    Delivery is **at-least-once, not exactly-once**, and that is a property of
    HTTP rather than a shortcut. When a request times out there is no way to know
    whether the destination processed it and the reply was lost, or whether it
    never arrived. Both outcomes look identical from here, and the only two
    options are to retry (risking a duplicate) or not to (risking a loss).
    Retrying is the right call, so duplicates are possible by design.

    `idempotency_key` is what makes that liveable: it stays the same across every
    attempt of the same delivery, so the receiver can recognise a repeat and
    ignore it. Generating a fresh one per attempt would make it useless, which is
    the mistake worth guarding against.
    """

    __tablename__ = "deliveries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    request_id: Mapped[int] = mapped_column(
        ForeignKey("requests.id", ondelete="CASCADE"), nullable=False
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("destinations.id", ondelete="CASCADE"), nullable=False
    )

    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DeliveryState.PENDING, server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    # NULL once the delivery is finished, one way or the other. The worker claims
    # work by looking for rows that are due, so a finished row simply stops being
    # findable.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stable across retries -- see the class docstring. This is the whole reason
    # at-least-once delivery is workable for the receiver.
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # One delivery per capture per destination. Without it, a retried enqueue
        # or two workers racing would forward the same capture twice with two
        # different idempotency keys -- which is exactly the duplicate the key
        # exists to prevent.
        UniqueConstraint("request_id", "destination_id", name="uq_deliveries_request_destination"),
        # The worker's only query is "what is due now?". A partial index over just
        # the pending rows stays small no matter how much delivered history piles
        # up behind it.
        Index(
            "ix_deliveries_due",
            "next_attempt_at",
            postgresql_where=(next_attempt_at.isnot(None)),
        ),
    )
