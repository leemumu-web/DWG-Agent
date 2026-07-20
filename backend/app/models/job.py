from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.platform.database.base import Base, PKType
from app.platform.database.mixins import TimestampMixin


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint(
            "created_by",
            "task_type",
            "request_key",
            name="uq_jobs_actor_task_request_key",
        ),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"))
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str | None] = mapped_column(String(128))
    precision_level: Mapped[str] = mapped_column(String(32), nullable=False)
    pipeline: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    priority: Mapped[int] = mapped_column(default=0, nullable=False)
    progress: Mapped[int] = mapped_column(default=0, nullable=False)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    progress_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list["JobStep"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class JobStep(Base):
    __tablename__ = "job_steps"
    __table_args__ = (Index("ix_job_steps_job_id_attempt", "job_id", "attempt"),)

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    worker_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    job: Mapped[Job] = relationship(back_populates="steps")
