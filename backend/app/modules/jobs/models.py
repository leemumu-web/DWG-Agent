from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    DECIMAL,
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
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
        UniqueConstraint(
            "task_type",
            "operation_key",
            name="uq_jobs_task_operation_key",
        ),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), index=True)
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"))
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_key: Mapped[str | None] = mapped_column(String(128))
    operation_key: Mapped[str | None] = mapped_column(String(191))
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


class JobDispatch(TimestampMixin, Base):
    __tablename__ = "job_dispatches"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "job_attempt",
            name="uq_job_dispatch_attempt",
        ),
        Index("ix_job_dispatch_pending", "status", "available_at"),
        Index("ix_job_dispatch_lease", "lease_expires_at"),
        Index("ix_job_dispatch_uid", "dispatch_uid"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    dispatch_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    job_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False)
    dispatch_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    celery_task_id: Mapped[str | None] = mapped_column(String(64))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(String(500))


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


class AnalysisResult(TimestampMixin, Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), index=True)
    result_type: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 4))
    result_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"))
    algorithm_version: Mapped[str | None] = mapped_column(String(64))
    tool_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="succeeded", nullable=False)

    reviews: Mapped[list["ReviewRecord"]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class ReviewRecord(TimestampMixin, Base):
    __tablename__ = "review_records"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_results.id"), nullable=False, index=True
    )
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"))
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    result: Mapped[AnalysisResult] = relationship(back_populates="reviews")
