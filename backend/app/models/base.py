"""Base declarativa compartida por todos los modelos."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Todas las tablas heredan de aquí.

    Alembic usa `Base.metadata` para detectar cambios en el esquema, así que un
    modelo que no herede de esta clase será invisible para las migraciones.
    """
