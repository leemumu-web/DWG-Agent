from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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


class DxfClassificationRun(TimestampMixin, Base):
    __tablename__ = "dxf_classification_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "job_attempt", name="uq_dxf_classification_job_attempt"),
        Index("ix_dxf_classification_workflow_attempt", "workflow_run_id", "job_attempt"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    job_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    classifier_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.2.0")
    report_schema: Mapped[str | None] = mapped_column(String(64))
    cli_schema: Mapped[str | None] = mapped_column(String(64))
    project_name: Mapped[str] = mapped_column(String(128), nullable=False)
    input_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classified_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_required_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unreadable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    type_counts_json: Mapped[dict[str, int] | None] = mapped_column(JSON)
    report_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    manifest_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["DxfClassificationItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="DxfClassificationItem.id"
    )


class DxfClassificationItem(TimestampMixin, Base):
    __tablename__ = "dxf_classification_items"
    __table_args__ = (
        UniqueConstraint("run_id", "source_file_id", name="uq_dxf_classification_run_source"),
        Index("ix_dxf_classification_items_disposition", "run_id", "disposition"),
        Index("ix_dxf_classification_items_part_type", "run_id", "part_type"),
        Index("ix_dxf_classification_items_group", "run_id", "group_key"),
        Index(
            "ix_dxf_classification_items_next_stage",
            "run_id",
            "next_stage_eligible",
        ),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("dxf_classification_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), index=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False, index=True)
    output_file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    output_name: Mapped[str] = mapped_column(String(255), nullable=False)
    output_directory: Mapped[str] = mapped_column(String(255), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    part_type: Mapped[str | None] = mapped_column(String(64))
    profile_raw: Mapped[str | None] = mapped_column(String(255))
    profile_normalized: Mapped[str | None] = mapped_column(String(255))
    type_source: Mapped[str | None] = mapped_column(String(32))
    group_key: Mapped[str] = mapped_column(String(96), nullable=False)
    next_stage_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    diagnostics_json: Mapped[list[str] | None] = mapped_column(JSON)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    run: Mapped[DxfClassificationRun] = relationship(back_populates="items")
