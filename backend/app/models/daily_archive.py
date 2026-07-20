from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKType
from app.models.mixins import TimestampMixin


class DailyArchiveRun(TimestampMixin, Base):
    __tablename__ = "daily_archive_runs"
    __table_args__ = (
        UniqueConstraint(
            "actor_user_id",
            "idempotency_key",
            name="uq_daily_archive_actor_idempotency",
        ),
        Index(
            "ix_daily_archive_scope_status",
            "archive_date",
            "scope_key",
            "status",
        ),
        Index("ix_daily_archive_manifest", "source_manifest_sha256"),
        Index("ix_daily_archive_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    archive_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_bucket: Mapped[str | None] = mapped_column(String(128))
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    actor_user_id: Mapped[int] = mapped_column(
        ForeignKey("sys_users.id"), nullable=False, index=True
    )
    source_file_ids_json: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    source_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket_counts_json: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    format_counts_json: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    archive_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), index=True
    )
    manifest_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("files.id", ondelete="SET NULL"), index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def source_file_ids(self) -> list[int]:
        return [int(value) for value in self.source_file_ids_json]

    @property
    def bucket_counts(self) -> dict[str, int]:
        return {str(key): int(value) for key, value in self.bucket_counts_json.items()}

    @property
    def format_counts(self) -> dict[str, int]:
        return {str(key): int(value) for key, value in self.format_counts_json.items()}
