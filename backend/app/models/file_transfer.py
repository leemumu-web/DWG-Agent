from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.database.base import Base, PKType
from app.platform.database.mixins import TimestampMixin


class FileTransfer(TimestampMixin, Base):
    __tablename__ = "file_transfers"
    __table_args__ = (
        UniqueConstraint("transfer_uid", name="uq_file_transfers_uid"),
        UniqueConstraint(
            "actor_user_id",
            "operation",
            "idempotency_key",
            name="uq_file_transfers_idempotency",
        ),
        Index("ix_file_transfers_direction_created", "direction", "created_at"),
        Index("ix_file_transfers_status_created", "status", "created_at"),
        Index("ix_file_transfers_operation_created", "operation", "created_at"),
    )

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    transfer_uid: Mapped[str] = mapped_column(String(36), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="prepared")
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"), index=True)
    batch_ref: Mapped[str | None] = mapped_column(String(64), index=True)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"), index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    bucket: Mapped[str | None] = mapped_column(String(128))
    storage_key: Mapped[str | None] = mapped_column(String(512))
    original_name: Mapped[str | None] = mapped_column(String(255))
    expected_bytes: Mapped[int | None] = mapped_column(BigInteger)
    transferred_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
