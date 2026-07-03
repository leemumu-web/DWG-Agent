from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKType
from app.models.mixins import TimestampMixin


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_users.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"))
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"))
    task: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    answer: Mapped[str | None] = mapped_column(Text)
    output_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"))
    history_count: Mapped[int] = mapped_column(default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["AgentRunStep"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )


class AgentRunStep(TimestampMixin, Base):
    __tablename__ = "agent_run_steps"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id"), nullable=False, index=True
    )
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    tool_name: Mapped[str | None] = mapped_column(String(128))
    arguments_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    agent_run: Mapped[AgentRun] = relationship(back_populates="steps")
