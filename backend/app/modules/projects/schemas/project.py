"""Project and membership HTTP contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Project code — letters, digits, underscore, hyphen only.",
    )
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    # 合法取值仅 active / deleted（与 routes 的集合常量一致）；
    # 写入其他值不会被拒绝，但项目策略按这两个状态判定。
    status: str | None = Field(default=None, max_length=32)


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    description: str | None = None
    owner_id: int | None = None
    owner_name: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectMemberCreate(BaseModel):
    user_id: int
    # 合法取值仅 project_owner / project_engineer（与 PROJECT_WRITE_ROLES /
    # PROJECT_OWNER_ROLES 一致）；项目成员策略依赖这两个角色集合判定
    # 写入与删除权限。
    project_role: str = Field(min_length=1, max_length=64)


class ProjectMemberUpdate(BaseModel):
    project_role: str = Field(min_length=1, max_length=64)


class ProjectMemberRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    user_id: int
    project_role: str
    created_at: datetime
