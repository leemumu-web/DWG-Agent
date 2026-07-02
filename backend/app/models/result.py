from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import DECIMAL, JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class AnalysisResult(TimestampMixin, Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False, index=True)
    drawing_id: Mapped[int | None] = mapped_column(ForeignKey("drawings.id"), index=True)
    result_type: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confidence: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 4))
    result_file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id"))
    algorithm_version: Mapped[str | None] = mapped_column(String(64))
    tool_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="succeeded", nullable=False)

    reviews: Mapped[list["ReviewRecord"]] = relationship(back_populates="result", cascade="all, delete-orphan")


class ReviewRecord(Base):
    __tablename__ = "review_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("analysis_results.id"), nullable=False, index=True)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"))
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    result: Mapped[AnalysisResult] = relationship(back_populates="reviews")
