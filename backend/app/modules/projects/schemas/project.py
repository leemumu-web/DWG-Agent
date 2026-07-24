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
