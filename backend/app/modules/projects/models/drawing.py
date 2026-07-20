from __future__ import annotations

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.platform.database.base import Base, PKType
from app.platform.database.mixins import TimestampMixin


class Drawing(TimestampMixin, Base):
    __tablename__ = "drawings"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    drawing_no: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(255))
    discipline: Mapped[str | None] = mapped_column(String(64))
    current_version_id: Mapped[int | None] = mapped_column(ForeignKey("drawing_versions.id"))
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)

    versions: Mapped[list["DrawingVersion"]] = relationship(
        "DrawingVersion",
        back_populates="drawing",
        cascade="all, delete-orphan",
        foreign_keys="DrawingVersion.drawing_id",
    )


class DrawingVersion(TimestampMixin, Base):
    __tablename__ = "drawing_versions"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    drawing_id: Mapped[int] = mapped_column(ForeignKey("drawings.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(nullable=False)
    source: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("sys_users.id"))

    drawing: Mapped[Drawing] = relationship(
        "Drawing",
        back_populates="versions",
        foreign_keys=[drawing_id],
    )
