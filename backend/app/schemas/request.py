"""Schemas for captured requests."""

import base64
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.models import CapturedRequest, SignatureCheck


class SignatureSummary(BaseModel):
    """Enough to draw a badge in a list: was it valid, and which case was it."""

    model_config = ConfigDict(from_attributes=True)

    provider: str
    valid: bool
    reason: str


class SignatureDetail(SignatureSummary):
    """The full verdict, including the sentence that actually helps.

    `detail` is the whole point of this feature. "Invalid signature" tells a
    developer nothing they did not already know; "the signature is genuine but
    the event is 400 seconds old" tells them exactly what to do next.
    """

    detail: str
    checked_at: datetime


class RequestSummary(BaseModel):
    """One row of the list view.

    Deliberately carries no body. A listing of fifty captures, each with a
    megabyte of payload, would be fifty megabytes of JSON to render a table that
    only shows method, path and time.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    method: str
    path: str
    content_type: str | None
    body_size: int
    body_truncated: bool
    source_ip: str | None
    received_at: datetime
    duration_ms: int | None
    # None when the endpoint has no provider configured, which is the common case.
    signature: SignatureSummary | None = None

    @classmethod
    def from_model(
        cls, captured: CapturedRequest, check: SignatureCheck | None = None
    ) -> "RequestSummary":
        """Build from a capture and its check, both passed in explicitly.

        The check is handed over rather than reached through a relationship on
        purpose. In async SQLAlchemy a lazily-loaded attribute fires a query from
        wherever it happens to be touched -- including inside a response
        serialiser, long after the session is gone. Passing it in makes every
        caller state where it came from.
        """
        summary = cls.model_validate(captured)
        if check is not None:
            summary.signature = SignatureSummary.model_validate(check)
        return summary

    @field_validator("source_ip", mode="before")
    @classmethod
    def _address_to_text(cls, value: object) -> object:
        """Turn the driver's address object into a plain string.

        The column is INET -- the right type, since Postgres validates the format
        and stores it compactly -- but psycopg returns it as an `IPv4Address`
        rather than text. The API contract stays a plain string, which is what a
        JSON consumer wants, so the conversion happens here at the boundary.
        """
        return None if value is None else str(value)


class RequestDetail(RequestSummary):
    """A single capture, with everything that arrived."""

    query: dict[str, str] | None
    headers: dict[str, str]
    body_json: Any | None
    # A body is arbitrary bytes and JSON cannot carry those. When it decodes as
    # UTF-8 it is returned as text, which is the common case and readable; when
    # it does not -- a protobuf or a gzip payload -- it is returned base64
    # encoded. Exactly one of the two is ever set, and `body_encoding` says which.
    body_text: str | None
    body_base64: str | None
    body_encoding: str | None
    # Narrower than the parent's on purpose: the detail view is the one place the
    # explanatory sentence belongs. Putting it in the summary too would carry a
    # paragraph per row through every listing, which is exactly what the summary
    # exists to avoid.
    signature: SignatureDetail | None = None

    @classmethod
    def from_model(
        cls, captured: CapturedRequest, check: SignatureCheck | None = None
    ) -> "RequestDetail":
        text: str | None = None
        encoded: str | None = None
        encoding: str | None = None

        if captured.body_raw is not None:
            try:
                text = captured.body_raw.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                encoded = base64.b64encode(captured.body_raw).decode("ascii")
                encoding = "base64"

        return cls(
            id=captured.id,
            method=captured.method,
            path=captured.path,
            content_type=captured.content_type,
            body_size=captured.body_size,
            body_truncated=captured.body_truncated,
            source_ip=captured.source_ip,
            received_at=captured.received_at,
            duration_ms=captured.duration_ms,
            query=captured.query,
            headers=captured.headers,
            body_json=captured.body_json,
            body_text=text,
            body_base64=encoded,
            body_encoding=encoding,
            signature=None if check is None else SignatureDetail.model_validate(check),
        )


class RequestPage(BaseModel):
    """A page of captures, newest first.

    Pagination is by cursor and not by offset. With `offset`, a capture arriving
    while the user pages pushes everything down by one, so the next page repeats
    a row and skips another. Since ids are monotonic, "give me what is older than
    id N" is stable no matter what arrives meanwhile.
    """

    items: list[RequestSummary]
    next_cursor: int | None
