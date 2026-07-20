"""Drawing-catalog HTTP contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DrawingCreate(BaseModel):
    project_id: int
    drawing_no: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=255)
    discipline: str | None = Field(default=None, max_length=64)
    file_id: int | None = None


class DrawingUpdate(BaseModel):
    drawing_no: str | None = Field(default=None, max_length=128)
    title: str | None = Field(default=None, max_length=255)
    discipline: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=32)


class DrawingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    drawing_no: str | None = None
    title: str | None = None
    discipline: str | None = None
    current_version_id: int | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class DrawingVersionCreate(BaseModel):
    file_id: int
    source: str | None = Field(default=None, max_length=64)


class DrawingVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    drawing_id: int
    file_id: int
    version_no: int
    source: str | None = None
    created_by: int | None = None
