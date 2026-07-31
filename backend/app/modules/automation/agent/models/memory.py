from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database.base import Base
from app.platform.time import business_now


class AgentMemory(Base):
    """Durable agent session history with an application-enforced TTL."""

    __tablename__ = "agent_memory"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: business_now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: business_now(),
        onupdate=lambda: business_now(),
        nullable=False,
    )
