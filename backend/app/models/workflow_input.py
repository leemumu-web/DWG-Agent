from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, PKType
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.workflow import WorkflowRun


class WorkflowInputBatch(TimestampMixin, Base):
    __tablename__ = "workflow_input_batches"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", name="uq_workflow_input_batch_workflow"),
        Index("ix_workflow_input_batches_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("sys_users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploading", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    workflow: Mapped[WorkflowRun] = relationship(back_populates="input_batch")
    items: Mapped[list[WorkflowInputItem]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="WorkflowInputItem.id",
    )


class WorkflowInputItem(TimestampMixin, Base):
    __tablename__ = "workflow_input_items"
    __table_args__ = (
        UniqueConstraint("input_batch_id", "file_id", name="uq_workflow_input_item_file"),
        Index("ix_workflow_input_items_batch_role", "input_batch_id", "role"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    input_batch_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_input_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_stem: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded", index=True)
    conversion_job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id"), index=True)
    conversion_job_attempt: Mapped[int | None] = mapped_column(Integer)
    derived_dxf_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)

    batch: Mapped[WorkflowInputBatch] = relationship(back_populates="items")
