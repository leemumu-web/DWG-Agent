from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database.base import Base, PKType
from app.platform.database.mixins import TimestampMixin


class StorageScanRun(TimestampMixin, Base):
    __tablename__ = "storage_scan_runs"
    __table_args__ = (
        Index("ix_storage_scan_runs_status_created", "status", "created_at"),
        Index("ix_storage_scan_runs_scope_status", "scope_bucket", "status"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_bucket: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"), index=True)
    scanned_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scanned_objects: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consistent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retained_deleted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_object_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    untracked_object_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    size_mismatch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StorageScanFinding(TimestampMixin, Base):
    __tablename__ = "storage_scan_findings"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "finding_type",
            "bucket",
            "storage_key",
            name="uq_storage_scan_finding_location",
        ),
        Index(
            "ix_storage_scan_findings_run_type",
            "run_id",
            "finding_type",
        ),
        Index(
            "ix_storage_scan_findings_run_resolution",
            "run_id",
            "resolution_status",
        ),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("storage_scan_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_type: Mapped[str] = mapped_column(String(32), nullable=False)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), index=True
    )
    file_status: Mapped[str | None] = mapped_column(String(32))
    database_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    object_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    object_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open"
    )
    resolution_action: Mapped[str | None] = mapped_column(String(32))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
