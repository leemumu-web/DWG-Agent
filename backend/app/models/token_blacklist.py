from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database.base import Base


class TokenBlacklist(Base):
    """Durable JWT revocation record, removed after its token expires."""

    __tablename__ = "token_blacklist"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
