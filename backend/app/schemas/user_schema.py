from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.constants import ACTIVE, DISABLED


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None = None
    is_system: bool = False


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
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8)
    real_name: str = Field(min_length=1, max_length=64)
    employee_no: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = None
    role_codes: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    real_name: str | None = Field(default=None, min_length=1, max_length=64)
    employee_no: str | None = Field(default=None, max_length=64)
    email: EmailStr | None = None
    status: Literal[ACTIVE, DISABLED] | None = None


class RoleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)


class AssignRoleRequest(BaseModel):
    role_code: str = Field(min_length=1, max_length=64)


class ReplaceRolePermissionsRequest(BaseModel):
    permission_codes: list[str]
