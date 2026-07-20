"""Domain-neutral FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.platform.database.session import get_db

DbSession = Annotated[Session, Depends(get_db)]

__all__ = ["DbSession", "get_db"]
