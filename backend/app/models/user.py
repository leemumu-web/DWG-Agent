from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.mixins import TimestampMixin
from app.models.role import user_roles
from app.platform.database.base import Base, PKType

if TYPE_CHECKING:
    from app.models.role import Role


class User(TimestampMixin, Base):
    __tablename__ = "sys_users"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    employee_no: Mapped[str | None] = mapped_column(String(64))
    real_name: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(128))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_algo: Mapped[str] = mapped_column(String(32), default="argon2id", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False, index=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    roles: Mapped[list["Role"]] = relationship(secondary=user_roles, back_populates="users")
