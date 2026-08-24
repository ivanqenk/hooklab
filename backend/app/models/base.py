"""Declarative base shared by every model."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Every table inherits from here.

    Alembic uses `Base.metadata` to detect schema changes, so a model that does
    not inherit from this class is invisible to migrations.
    """
