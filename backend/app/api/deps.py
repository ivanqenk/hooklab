"""Shared route dependencies.

They are expressed as `Annotated` type aliases rather than `Depends()` default
values. Both work in FastAPI, but the annotated form keeps the dependency inside
the type, so a route signature reads as plain typed parameters, the alias is
reusable across routers, and it does not trip the general Python rule against
calling functions in argument defaults.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
