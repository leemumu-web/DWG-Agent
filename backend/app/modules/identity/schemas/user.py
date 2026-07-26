"""User, role and permission HTTP contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.platform.config.constants import ACTIVE, DISABLED

# Common passwords banned in production — prevents the laziest brute-force wins.
_COMMON_PASSWORDS = frozenset({
    "password", "password123", "admin123", "admin123456", "12345678",
    "123456789", "qwerty123", "abc12345", "letmein12", "welcome123",
    "changeme12", "pass1234", "pass12345", "pass123456",
    "Password123", "Admin123456", "Qwerty1234",
})

_HTML_TAG_RE = re.compile(r"<\s*[a-zA-Z/]")


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None = None
    is_system: bool = False
    permissions: list["PermissionRead"] = []


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    resource: str
    action: str
    name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    employee_no: str | None = None
    real_name: str
    email: str | None = None
    status: str
    roles: list[RoleRead] = []
    created_at: datetime
    updated_at: datetime


class UserCreate(BaseModel):
    username: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_.@-]+$",
        description="Username — letters, digits, underscore, dot, at-sign, hyphen only.",
    )
    password: str = Field(
        min_length=12,
        description="Password — minimum 12 characters, must contain upper + lower + digit.",
    )
    real_name: str = Field(min_length=1, max_length=64)
    employee_no: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = None

    @field_validator("username")
    @classmethod
    def _trim_username(cls, v: str) -> str:
        return v.strip()

    @field_validator("real_name")
    @classmethod
    def _reject_html_in_real_name(cls, v: str) -> str:
        if _HTML_TAG_RE.search(v):
            raise ValueError("real_name contains HTML — not allowed.")
        return v

    @field_validator("password")
    @classmethod
    def _enforce_password_complexity(cls, v: str) -> str:
        if v.lower() in _COMMON_PASSWORDS:
            raise ValueError("This password is too common — choose a stronger one.")
        if not (any(c.islower() for c in v) and any(c.isupper() for c in v) and any(c.isdigit() for c in v)):
            raise ValueError(
                "Password must contain at least one uppercase letter, "
                "one lowercase letter, and one digit."
            )
        return v


class UserUpdate(BaseModel):
    real_name: str | None = Field(default=None, min_length=1, max_length=64)
    employee_no: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = None
    status: Literal[ACTIVE, DISABLED] | None = None

    @field_validator("real_name")
    @classmethod
    def _reject_html_in_real_name(cls, v: str | None) -> str | None:
        if v is not None and _HTML_TAG_RE.search(v):
            raise ValueError("real_name contains HTML — not allowed.")
        return v


class UserSelfUpdate(BaseModel):
    """Fields a user may update on their own profile — status changes excluded."""

    real_name: str | None = Field(default=None, min_length=1, max_length=64)
    email: EmailStr | None = None

    @field_validator("real_name")
    @classmethod
    def _reject_html_in_real_name(cls, v: str | None) -> str | None:
        if v is not None and _HTML_TAG_RE.search(v):
            raise ValueError("real_name contains HTML — not allowed.")
        return v


class AdminPasswordResetRequest(BaseModel):
    """Administrator-selected replacement password; never generated or returned."""

    new_password: str = Field(
        min_length=12,
        description="Password — minimum 12 characters, must contain upper + lower + digit.",
    )

    @field_validator("new_password")
    @classmethod
    def _enforce_password_complexity(cls, v: str) -> str:
        if v.lower() in _COMMON_PASSWORDS:
            raise ValueError("This password is too common — choose a stronger one.")
        if not (
            any(c.islower() for c in v)
            and any(c.isupper() for c in v)
            and any(c.isdigit() for c in v)
        ):
            raise ValueError(
                "Password must contain at least one uppercase letter, "
                "one lowercase letter, and one digit."
            )
        return v


class RoleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)


class AssignRoleRequest(BaseModel):
    role_code: str = Field(min_length=1, max_length=64)


class ReplaceRolePermissionsRequest(BaseModel):
    permission_codes: list[str]
