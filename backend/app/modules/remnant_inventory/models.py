from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database.base import Base, PKType
from app.platform.database.mixins import TimestampMixin


class RemnantMaterial(TimestampMixin, Base):
    __tablename__ = "remnant_materials"
    __table_args__ = (
        UniqueConstraint("code", name="uq_remnant_material_code"),
        Index("ix_remnant_material_family_enabled", "family_code", "enabled"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    family_code: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[int | None] = mapped_column(PKType, ForeignKey("sys_users.id"))
    updated_by: Mapped[int | None] = mapped_column(PKType, ForeignKey("sys_users.id"))


class RemnantMaterialAlias(TimestampMixin, Base):
    __tablename__ = "remnant_material_aliases"
    __table_args__ = (
        UniqueConstraint("normalized_alias", name="uq_remnant_material_alias_normalized"),
        Index("ix_remnant_material_alias_material", "material_id"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    material_id: Mapped[int] = mapped_column(
        PKType, ForeignKey("remnant_materials.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[int | None] = mapped_column(PKType, ForeignKey("sys_users.id"))


class RemnantImportBatch(TimestampMixin, Base):
    __tablename__ = "remnant_import_batches"
    __table_args__ = (
        CheckConstraint(
            "import_mode IN ('manual', 'auto')",
            name="ck_remnant_import_batch_mode",
        ),
        Index("ix_remnant_import_batch_creator_status", "created_by", "status"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    created_by: Mapped[int] = mapped_column(PKType, ForeignKey("sys_users.id"), nullable=False)
    import_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    default_project_no: Mapped[str | None] = mapped_column(String(128))
    source_folder_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    converting_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RemnantImportItem(TimestampMixin, Base):
    __tablename__ = "remnant_import_items"
    __table_args__ = (
        Index("ix_remnant_import_item_batch_status", "batch_id", "status"),
        Index("ix_remnant_import_item_source_sha", "source_sha256"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        PKType, ForeignKey("remnant_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    source_file_id: Mapped[int] = mapped_column(PKType, ForeignKey("files.id"), nullable=False)
    dxf_file_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("files.id"))
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ext: Mapped[str] = mapped_column(String(8), nullable=False)
    source_relative_path: Mapped[str | None] = mapped_column(String(1024))
    conversion_job_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("jobs.id"))
    parse_job_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("jobs.id"))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    parser_version: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[str | None] = mapped_column(String(16))
    material_candidates_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    project_candidates_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    part_candidates_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    warnings_json: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    standard_parse_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    corrected_thickness_mm: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    corrected_material_id: Mapped[int | None] = mapped_column(
        PKType, ForeignKey("remnant_materials.id")
    )
    corrected_project_no: Mapped[str | None] = mapped_column(String(128))
    corrected_project_no_secondary: Mapped[str | None] = mapped_column(String(128))
    corrected_storage_location: Mapped[str | None] = mapped_column(String(128))
    corrected_remark_1: Mapped[str | None] = mapped_column(String(500))
    corrected_remark_2: Mapped[str | None] = mapped_column(String(500))
    corrected_parts_json: Mapped[list[str] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class Remnant(TimestampMixin, Base):
    __tablename__ = "remnants"
    __table_args__ = (
        UniqueConstraint("source_sha256", name="uq_remnant_source_sha256"),
        UniqueConstraint("import_item_id", name="uq_remnant_import_item_confirmation"),
        Index("ix_remnant_search", "material_id", "thickness_mm", "status"),
        Index("ix_remnant_reserved_by_status", "reserved_by", "status"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    import_item_id: Mapped[int] = mapped_column(
        PKType, ForeignKey("remnant_import_items.id"), nullable=False
    )
    source_file_id: Mapped[int] = mapped_column(PKType, ForeignKey("files.id"), nullable=False)
    dxf_file_id: Mapped[int] = mapped_column(PKType, ForeignKey("files.id"), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    thickness_mm: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    material_id: Mapped[int] = mapped_column(
        PKType, ForeignKey("remnant_materials.id"), nullable=False
    )
    project_no: Mapped[str] = mapped_column(String(128), nullable=False)
    project_no_secondary: Mapped[str | None] = mapped_column(String(128))
    storage_location: Mapped[str | None] = mapped_column(String(128))
    remark_1: Mapped[str | None] = mapped_column(String(500))
    remark_2: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")
    imported_by: Mapped[int] = mapped_column(PKType, ForeignKey("sys_users.id"), nullable=False)
    confirmed_by: Mapped[int] = mapped_column(PKType, ForeignKey("sys_users.id"), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_by: Mapped[int | None] = mapped_column(PKType, ForeignKey("sys_users.id"))
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(PKType, ForeignKey("sys_users.id"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[int | None] = mapped_column(PKType, ForeignKey("sys_users.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class RemnantPart(TimestampMixin, Base):
    __tablename__ = "remnant_parts"
    __table_args__ = (
        UniqueConstraint("remnant_id", "part_no", name="uq_remnant_part_number"),
        Index("ix_remnant_part_number", "part_no"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    remnant_id: Mapped[int] = mapped_column(
        PKType, ForeignKey("remnants.id", ondelete="CASCADE"), nullable=False
    )
    part_no: Mapped[str] = mapped_column(String(128), nullable=False)


__all__ = [
    "Remnant",
    "RemnantImportBatch",
    "RemnantImportItem",
    "RemnantMaterial",
    "RemnantMaterialAlias",
    "RemnantPart",
]
