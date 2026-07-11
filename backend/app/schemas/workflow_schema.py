from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

WORKFLOW_TYPES = {"excel_delivery", "file_delivery"}


class WorkflowCreate(BaseModel):
    project_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=128)
    workflow_type: str = Field(default="excel_delivery", max_length=64)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Workflow name must not be blank.")
        return normalized

    @field_validator("workflow_type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in WORKFLOW_TYPES:
            raise ValueError(f"Unsupported workflow type: {value}")
        return value


class WorkflowStageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage_code: str
    name: str
    sequence: int
    status: str
    job_id: int | None = None
    job_attempt: int | None = None
    progress: int
    input_json: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    stage_run_id: int | None = None
    artifact_type: str
    file_id: int | None = None
    result_id: int | None = None
    version: int
    metadata_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    created_by: int
    name: str
    workflow_type: str
    status: str
    current_stage: str | None = None
    progress: int
    config_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowDetail(WorkflowRead):
    stages: list[WorkflowStageRead]
    artifacts: list[WorkflowArtifactRead]
