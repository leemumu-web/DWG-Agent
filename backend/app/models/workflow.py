from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.platform.database.base import Base, PKType
from app.platform.database.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.workflow_input import WorkflowInputBatch


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_runs_project_status", "project_id", "status"),
        Index("ix_workflow_runs_created_by_status", "created_by", "status"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("sys_users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False, default="excel_delivery")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    current_stage: Mapped[str | None] = mapped_column(String(64))
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    stages: Mapped[list["WorkflowStageRun"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", order_by="WorkflowStageRun.sequence"
    )
    artifacts: Mapped[list["WorkflowArtifact"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    input_batch: Mapped[WorkflowInputBatch | None] = relationship(
        back_populates="workflow", cascade="all, delete-orphan", uselist=False
    )


class WorkflowStageRun(TimestampMixin, Base):
    __tablename__ = "workflow_stage_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "stage_code", name="uq_workflow_stage_code"),
        Index("ix_workflow_stage_runs_workflow_sequence", "workflow_run_id", "sequence"),
        Index("ix_workflow_stage_runs_job_attempt", "job_id", "job_attempt"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    job_attempt: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workflow: Mapped[WorkflowRun] = relationship(back_populates="stages")
    artifacts: Mapped[list["WorkflowArtifact"]] = relationship(back_populates="stage")


class WorkflowArtifact(TimestampMixin, Base):
    __tablename__ = "workflow_artifacts"
    __table_args__ = (
        Index("ix_workflow_artifacts_workflow_type", "workflow_run_id", "artifact_type"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_stage_runs.id", ondelete="SET NULL"), index=True
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    result_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_results.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    workflow: Mapped[WorkflowRun] = relationship(back_populates="artifacts")
    stage: Mapped[WorkflowStageRun | None] = relationship(back_populates="artifacts")
