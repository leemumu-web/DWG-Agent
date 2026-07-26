"""Persistent manifests for selective exports and whole-Workflow retention."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database.base import Base, PKType
from app.platform.database.mixins import TimestampMixin


class WorkflowBatchExport(TimestampMixin, Base):
    __tablename__ = "workflow_batch_exports"
    __table_args__ = (
        UniqueConstraint("export_uid", name="uq_workflow_batch_exports_uid"),
        Index(
            "ix_workflow_batch_exports_workflow_status",
            "workflow_run_id",
            "status",
        ),
        Index(
            "ix_workflow_batch_exports_creator_created",
            "created_by",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    export_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("sys_users.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="prepared",
        index=True,
    )
    categories_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    manifest_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    token_digest: Mapped[str | None] = mapped_column(String(64))
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    file_count: Mapped[int] = mapped_column(nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_file_count: Mapped[int] = mapped_column(nullable=False, default=0)
    purged_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


class WorkflowRetentionExport(TimestampMixin, Base):
    __tablename__ = "workflow_retention_exports"
    __table_args__ = (
        UniqueConstraint("export_uid", name="uq_workflow_retention_exports_uid"),
        Index(
            "ix_workflow_retention_exports_workflow_status",
            "workflow_run_id",
            "status",
        ),
        Index(
            "ix_workflow_retention_exports_creator_created",
            "created_by",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    export_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by: Mapped[int] = mapped_column(
        ForeignKey("sys_users.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="prepared",
        index=True,
    )
    manifest_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    token_digest: Mapped[str | None] = mapped_column(String(64))
    token_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    file_count: Mapped[int] = mapped_column(nullable=False)
    preview_cache_count: Mapped[int] = mapped_column(nullable=False, default=0)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reclaimable_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    purge_transfer_uid: Mapped[str | None] = mapped_column(String(36), index=True)
    purge_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_file_count: Mapped[int] = mapped_column(nullable=False, default=0)
    purged_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)


__all__ = ["WorkflowBatchExport", "WorkflowRetentionExport"]
