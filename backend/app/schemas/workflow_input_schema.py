from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.file_schema import FileRead
from app.schemas.job_schema import JobRead


class WorkflowInputFileCreate(BaseModel):
    file_id: int = Field(gt=0)


class WorkflowInputCounts(BaseModel):
    dwg: int = Field(ge=0)
    excel: int = Field(ge=0)
    paired: int = Field(ge=0)
    converting: int = Field(ge=0)
    failed: int = Field(ge=0)


class WorkflowInputIssueRead(BaseModel):
    item_id: int | None = None
    file_name: str | None = None
    code: str
    message: str
    recommended_action: str


class WorkflowInputItemRead(BaseModel):
    id: int
    role: str
    status: str
    original_name: str
    normalized_stem: str
    file: FileRead
    conversion_job: JobRead | None = None
    derived_dxf: FileRead | None = None
    drawing_id: int | None = None
    error_code: str | None = None
    error_message: str | None = None


class WorkflowInputBatchRead(BaseModel):
    id: int
    workflow_run_id: int
    project_id: int
    status: str
    version: int
    manifest_sha256: str | None = None
    frozen_at: datetime | None = None
    counts: WorkflowInputCounts
    items: list[WorkflowInputItemRead]
    issues: list[WorkflowInputIssueRead]
    freeze_ready: bool
    created_at: datetime
    updated_at: datetime


class WorkflowInputConversionRead(BaseModel):
    batch: WorkflowInputBatchRead
    jobs: list[JobRead]
    dispatched_count: int = Field(ge=0)
