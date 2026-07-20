from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.mixins import TimestampMixin, utcnow
from app.platform.database.base import Base, PKType


class WorkerRuntime(TimestampMixin, Base):
    """Best-effort worker lifecycle record; not a replacement for a broker lease."""

    __tablename__ = "worker_runtimes"
    __table_args__ = (
        Index("ix_worker_runtimes_status_last_seen", "status", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    worker_name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255))
    process_id: Mapped[int | None] = mapped_column(Integer)
    queues_json: Mapped[list[str] | None] = mapped_column(JSON)
    concurrency: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="starting", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class ControlPlaneEvent(Base):
    """Append-only operational events, including future agent protocol directions."""

    __tablename__ = "control_plane_events"
    __table_args__ = (
        Index("ix_control_plane_events_created", "created_at"),
        Index("ix_control_plane_events_target", "target_kind", "target_id"),
        Index("ix_control_plane_events_correlation", "correlation_id"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(32), default="internal", nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    target_kind: Mapped[str | None] = mapped_column(String(48))
    target_id: Mapped[str | None] = mapped_column(String(160))
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PlatformMessage(TimestampMixin, Base):
    """Administrator-visible operational message projected from control-plane events."""

    __tablename__ = "platform_messages"
    __table_args__ = (Index("ix_platform_messages_status_created", "status", "created_at"),)

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    audience: Mapped[str] = mapped_column(String(32), default="admins", nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info", nullable=False)
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="unread", nullable=False)
    action_url: Mapped[str | None] = mapped_column(String(512))
    related_event_id: Mapped[int | None] = mapped_column(ForeignKey("control_plane_events.id"), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
