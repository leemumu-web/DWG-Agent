"""SQLAlchemy models owned by the Excel Final relationship projection.

Three tables mirror the Excel output sheets:
  - excel_final_batches    — processing run metadata
  - excel_final_parts      — 整理表 (35-column canonical part list projection)
  - excel_final_components — 构件表 (component summary)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.platform.database.base import Base, PKType

PHYSICAL_VALUE_TYPE = DECIMAL(precision=24, scale=9)


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
        String(32),
        nullable=False,
        default="init",
        comment="init / canonical / tsv",
    )
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    component_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    part_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_net_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    total_gross_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
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
    original_qty: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    component_no: Mapped[str | None] = mapped_column(String(512), nullable=True)
    component_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    part_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    part_no: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    profile_spec: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spec: Mapped[str | None] = mapped_column(String(128), nullable=True)
    width: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    length: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    left_inset: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    right_inset: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    cut_length: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    material: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    qty: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    total_qty: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    total_length: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    density: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    density_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    theo_unit_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    theo_total_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    material_utilization: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    weight_validation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    net_unit_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    net_total_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    table_net_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    gross_unit_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    gross_total_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    table_gross_weight: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
    surface_area: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    total_surface_area: Mapped[Decimal | None] = mapped_column(
        PHYSICAL_VALUE_TYPE, nullable=True
    )
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
    total_weight: Mapped[Decimal | None] = mapped_column(PHYSICAL_VALUE_TYPE, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped[ExcelFinalBatch] = relationship("ExcelFinalBatch", back_populates="components")
