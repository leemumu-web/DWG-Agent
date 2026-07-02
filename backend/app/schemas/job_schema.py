from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobCreate(BaseModel):
    drawing_id: int | None = None
    project_id: int | None = None
    task_type: str = Field(default="framework_smoke_test")
    precision_level: str = Field(default="normal")
    params: dict[str, Any] = Field(default_factory=dict)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int | None = None
    drawing_id: int | None = None
    created_by: int | None = None
    task_type: str
    precision_level: str
    pipeline: str | None = None
    status: str
    priority: int
    progress: int
    params_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_id: int
    step_name: str
    worker_name: str | None = None
    status: str
    input_json: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
