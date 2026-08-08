"""Plate classification database models."""

from __future__ import annotations

from datetime import datetime

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


class PlateClassificationRun(TimestampMixin, Base):
    """一次板件分类运行记录。"""

    __tablename__ = "plate_classification_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "job_attempt", name="uq_plate_class_job_attempt"),
        Index("ix_plate_class_workflow_attempt", "workflow_run_id", "job_attempt"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), nullable=False, index=True
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("jobs.id"), nullable=False, index=True
    )
    job_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="running", index=True
    )
    classifier_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="0.1.0"
    )
    project_name: Mapped[str] = mapped_column(String(128), nullable=False)
    input_directory: Mapped[str] = mapped_column(String(512), nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    classified_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 8 种类别计数 (JSON: {"方": 5, "异孔折": 3, ...})
    category_counts_json: Mapped[dict[str, int] | None] = mapped_column(JSON)
    report_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id"), index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["PlateClassificationItem"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="PlateClassificationItem.id"
    )


class PlateClassificationItem(TimestampMixin, Base):
    """单块板的分类结果。"""

    __tablename__ = "plate_classification_items"
    __table_args__ = (
        Index("ix_plate_class_items_run", "run_id"),
        Index("ix_plate_class_items_category", "run_id", "category"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("plate_classification_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    part_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dxf_file: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    shape: Mapped[str] = mapped_column(String(8), nullable=False)  # 方/异
    hole: Mapped[str] = mapped_column(String(8), nullable=False)  # 有孔/无孔
    bend: Mapped[str] = mapped_column(String(8), nullable=False)  # 有折/无折

    run: Mapped[PlateClassificationRun] = relationship(back_populates="items")
