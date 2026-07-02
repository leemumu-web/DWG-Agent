from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


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
    username: str
    password: str
    real_name: str
    employee_no: str | None = None
    email: EmailStr | None = None
    role_codes: list[str] = []


class UserUpdate(BaseModel):
    real_name: str | None = None
    employee_no: str | None = None
    email: EmailStr | None = None
    status: str | None = None


class RoleCreate(BaseModel):
    code: str
    name: str
    description: str | None = None


class AssignRoleRequest(BaseModel):
    role_code: str


class ReplaceRolePermissionsRequest(BaseModel):
    permission_codes: list[str]
