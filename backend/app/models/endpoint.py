"""Modelo de un endpoint de captura."""

import secrets
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 16 bytes aleatorios → 22 caracteres en base64url. Espacio de búsqueda suficiente
# para que adivinar un token sea inviable, y corto para que la URL siga siendo
# cómoda de pegar en el panel de configuración de un proveedor.
TOKEN_BYTES = 16


def generar_token() -> str:
    """Token aleatorio criptográficamente seguro y seguro para URLs."""
    return secrets.token_urlsafe(TOKEN_BYTES)


class Endpoint(Base):
    """Una URL de captura.

    Lleva DOS tokens distintos a propósito, y es una decisión de seguridad:

    - `ingest_token` es PÚBLICO: viaja en la URL que pegas en la configuración de
      Stripe o GitHub, así que acaba en logs, capturas de pantalla y tickets.
    - `view_token` es SECRETO: es el único que permite *leer* el tráfico capturado.

    Si fueran el mismo, cualquiera que viera esa URL en una configuración podría
    leer todos tus payloads — que suelen incluir credenciales y datos de clientes.
    """

    __tablename__ = "endpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    ingest_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=generar_token
    )
    view_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=generar_token
    )

    # NULL = endpoint anónimo. Se prevé desde ahora aunque las cuentas lleguen
    # más adelante: agregar la columna después obligaría a migrar datos existentes.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    nombre: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Los anónimos caducan; el worker de retención los borra al vencer.
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    request_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
