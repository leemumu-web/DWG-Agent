"""SQLAlchemy models owned by the Excel Final relationship projection.

Three tables mirror the Excel output sheets:
  - excel_final_batches    — processing run metadata
  - excel_final_parts      — 整理表 (35-column canonical part list projection)
  - excel_final_components — 构件表 (component summary)
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.platform.database.base import Base, PKType


class ExcelFinalBatch(Base):
    __tablename__ = "excel_final_batches"
    __table_args__ = (UniqueConstraint("job_id", name="uq_excel_final_batches_job_id"),)

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        PKType, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    file_id: Mapped[int | None] = mapped_column(
        PKType, ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="init_table", comment="init_table / tekla_tsv"
    )
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    component_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    part_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_net_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_gross_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    severe_warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    report_summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    parts: Mapped[list[ExcelFinalPart]] = relationship(
        "ExcelFinalPart", back_populates="batch", cascade="all, delete-orphan"
    )
    components: Mapped[list[ExcelFinalComponent]] = relationship(
        "ExcelFinalComponent", back_populates="batch", cascade="all, delete-orphan"
    )


class ExcelFinalPart(Base):
    __tablename__ = "excel_final_parts"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        PKType,
        ForeignKey("excel_final_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    import_component_no: Mapped[str | None] = mapped_column(String(512), nullable=True)
    import_part_no: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_batch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    team: Mapped[str | None] = mapped_column(String(128), nullable=True)
    original_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    component_no: Mapped[str | None] = mapped_column(String(512), nullable=True)
    component_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    part_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    part_no: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    profile_spec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spec: Mapped[str | None] = mapped_column(String(128), nullable=True)
    width: Mapped[float | None] = mapped_column(Float, nullable=True)
    length: Mapped[float | None] = mapped_column(Float, nullable=True)
    left_inset: Mapped[float | None] = mapped_column(Float, nullable=True)
    right_inset: Mapped[float | None] = mapped_column(Float, nullable=True)
    cut_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    material: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    density: Mapped[float | None] = mapped_column(Float, nullable=True)
    density_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    theo_unit_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    theo_total_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    material_utilization: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_validation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    net_unit_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_total_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    table_net_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_unit_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    gross_total_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    table_gross_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    surface_area: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_surface_area: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped[ExcelFinalBatch] = relationship("ExcelFinalBatch", back_populates="parts")


class ExcelFinalComponent(Base):
    __tablename__ = "excel_final_components"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        PKType,
        ForeignKey("excel_final_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    component_no: Mapped[str | None] = mapped_column(String(512), nullable=True)
    component_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped[ExcelFinalBatch] = relationship("ExcelFinalBatch", back_populates="components")
