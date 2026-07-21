"""Workflow, stage, template and artifact API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

WORKFLOW_TYPES = {"excel_delivery", "file_delivery", "linux_production"}


class WorkflowStageCapability(BaseModel):
    code: str
    name: str
    description: str
    execution_mode: Literal["manual", "automated", "placeholder", "external"]
    implementation_status: Literal["implemented", "placeholder", "external"]
    execution_kind: str | None = None
    required_inputs: list[str] = Field(default_factory=list)
    artifact_types: list[str] = Field(default_factory=list)


class WorkflowTemplateRead(BaseModel):
    code: str
    name: str
    description: str
    stages: list[WorkflowStageCapability]


class WorkflowArtifactCreate(BaseModel):
    stage_code: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    artifact_type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    file_id: int | None = Field(default=None, gt=0)
    result_id: int | None = Field(default=None, gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("result_id")
    @classmethod
    def require_reference(cls, value: int | None, info):
        if value is None and info.data.get("file_id") is None:
            raise ValueError("An artifact must reference file_id or result_id.")
        return value


class WorkflowStageExecutionCreate(BaseModel):
    execution_kind: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    batch_name: str | None = Field(default=None, max_length=255)
    file_id: int | None = Field(default=None, gt=0)

    @field_validator("batch_name")
    @classmethod
    def normalize_batch_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


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
