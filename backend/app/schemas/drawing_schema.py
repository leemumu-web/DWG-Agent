from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DrawingCreate(BaseModel):
    project_id: int
    drawing_no: str | None = None
    title: str | None = None
    discipline: str | None = None
    file_id: int | None = None


class DrawingUpdate(BaseModel):
    drawing_no: str | None = None
    title: str | None = None
    discipline: str | None = None
    status: str | None = None


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
    source: str | None = None


class DrawingVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    drawing_id: int
    file_id: int
    version_no: int
    source: str | None = None
    created_by: int | None = None
