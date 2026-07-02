from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from backend.app.models.user import User

user_roles = Table(
    "sys_user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("sys_users.id"), primary_key=True),
    Column("role_id", ForeignKey("sys_roles.id"), primary_key=True),
)

role_permissions = Table(
    "sys_role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("sys_roles.id"), primary_key=True),
    Column("permission_id", ForeignKey("sys_permissions.id"), primary_key=True),
)


class Role(TimestampMixin, Base):
    __tablename__ = "sys_roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    permissions: Mapped[list["Permission"]] = relationship(secondary=role_permissions, back_populates="roles")
    users: Mapped[list["User"]] = relationship(secondary=user_roles, back_populates="roles")


class Permission(Base):
    __tablename__ = "sys_permissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    roles: Mapped[list[Role]] = relationship(secondary=role_permissions, back_populates="permissions")
