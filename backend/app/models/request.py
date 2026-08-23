"""Modelo de una petición HTTP capturada."""

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
    """Una petición HTTP capturada, tal como llegó.

    Sobre el cuerpo: se guarda SIEMPRE en crudo (`body_raw`), y solo se guarda una
    versión parseada (`body_json`) cuando el content-type lo justifica y el parseo
    no falla. Son dos razones distintas:

    1. Los webhooks reales mandan XML, form-encoded, multipart y binario, no solo JSON.
    2. Más importante: el HMAC de una firma se calcula sobre los bytes EXACTOS del
       cuerpo. Si guardáramos solo la versión parseada y la volviéramos a serializar,
       la verificación de firma fallaría aunque el secreto fuera correcto.
    """

    __tablename__ = "requests"

    # BigInteger autoincremental, y no un UUID, a propósito: al ser monotónico sirve
    # tal cual como `Last-Event-ID` del SSE. El navegador reconecta diciendo "vengo
    # del 4711" y se le entrega lo que falta, sin necesidad de una tabla de cursores.
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
        # La consulta dominante es "dame las últimas N peticiones de este endpoint".
        # Un índice compuesto que arranca por endpoint_id la resuelve con un recorrido
        # de rango; Postgres puede recorrerlo hacia atrás para el ORDER BY id DESC,
        # así que no hace falta declarar la dirección.
        Index("ix_requests_endpoint_id_id", "endpoint_id", "id"),
    )
