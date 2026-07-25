from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
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


class DxfSplitRun(TimestampMixin, Base):
    __tablename__ = "dxf_split_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "job_attempt", name="uq_dxf_split_job_attempt"),
        Index("ix_dxf_split_workflow_attempt", "workflow_run_id", "job_attempt"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    classification_run_id: Mapped[int] = mapped_column(
        ForeignKey("dxf_classification_runs.id"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    job_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    splitter_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.5.2")
    cli_schema: Mapped[str | None] = mapped_column(String(64))
    validation_schema: Mapped[str | None] = mapped_column(String(64))
    input_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    auto_accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual_review_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_contracts_json: Mapped[dict[str, str] | None] = mapped_column(JSON)
    bh_split_ledger_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    split_manifest_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    validation_report_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id"), index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["DxfSplitItem"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="DxfSplitItem.id",
    )


class DxfSplitItem(TimestampMixin, Base):
    __tablename__ = "dxf_split_items"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "classification_item_id",
            name="uq_dxf_split_run_classification_item",
        ),
        Index("ix_dxf_split_items_route", "run_id", "automation_route"),
        Index("ix_dxf_split_items_part_type", "run_id", "part_type"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("dxf_split_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    classification_item_id: Mapped[int] = mapped_column(
        ForeignKey("dxf_classification_items.id"), nullable=False, index=True
    )
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), index=True)
    source_file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    part_type: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_normalized: Mapped[str | None] = mapped_column(String(255))
    family: Mapped[str | None] = mapped_column(String(16))
    source_contract_id: Mapped[str | None] = mapped_column(String(64))
    automation_route: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    disposition: Mapped[str] = mapped_column(String(64), nullable=False)
    normal_dxf_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    weld_allowance_dxf_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id"), index=True
    )
    split_report_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    weld_allowance_report_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id"), index=True
    )
    diagnostics_json: Mapped[list[str] | None] = mapped_column(JSON)
    validation_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    run: Mapped[DxfSplitRun] = relationship(back_populates="items")
