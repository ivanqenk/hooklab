"""Modelos de datos.

Importar aquí todos los modelos es necesario para que Alembic los vea: la
autogeneración compara la base real contra `Base.metadata`, y un modelo que
nadie haya importado sencillamente no está registrado ahí.
"""

from app.models.base import Base
from app.models.endpoint import Endpoint
from app.models.request import Request

__all__ = ["Base", "Endpoint", "Request"]
