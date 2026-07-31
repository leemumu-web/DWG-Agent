"""Production input ledger, diagnostic and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.modules.files.interface import FileRead
from app.modules.jobs.interface import JobRead


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
    failure: dict[str, Any] | None = None


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
    validation: dict[str, Any] | None = None
    validation_contract_version: int | None = None
    validated_sha256: str | None = None


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
    item_total: int = Field(ge=0)
    item_page: int = Field(ge=1)
    item_page_size: int = Field(ge=1, le=100)
    issues: list[WorkflowInputIssueRead]
    freeze_ready: bool
    recoverable_file_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime


class WorkflowInputConversionRead(BaseModel):
    batch: WorkflowInputBatchRead
    jobs: list[JobRead]
    dispatched_count: int = Field(ge=0)


class WorkflowInputResponseMeta(BaseModel):
    request_id: str
    timestamp: datetime


class WorkflowInputBatchEnvelope(BaseModel):
    data: WorkflowInputBatchRead
    meta: WorkflowInputResponseMeta


class WorkflowInputRegistrationRead(BaseModel):
    batch: WorkflowInputBatchRead
    item_id: int
    reused: bool


class WorkflowInputRegistrationEnvelope(BaseModel):
    data: WorkflowInputRegistrationRead
    meta: WorkflowInputResponseMeta


class WorkflowInputConversionEnvelope(BaseModel):
    data: WorkflowInputConversionRead
    meta: WorkflowInputResponseMeta
