"""HTTP contracts for complete Workflow backup and permanent retention cleanup."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowRetentionPreviewRead(BaseModel):
    workflow_id: int
    workflow_status: str
    terminal: bool
    blocked: bool
    blockers: list[dict[str, Any]]
    file_count: int
    preview_cache_count: int
    source_size_bytes: int
    reclaimable_size_bytes: int


class WorkflowRetentionExportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    export_uid: str
    workflow_run_id: int
    status: str
    file_count: int
    preview_cache_count: int
    source_size_bytes: int
    reclaimable_size_bytes: int
    filename: str
    download_url: str | None = None
    token_expires_at: datetime
    downloaded_at: datetime | None = None
    task_id: str | None = None
    purge_started_at: datetime | None = None
    purged_at: datetime | None = None
    purged_file_count: int = 0
    purged_size_bytes: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowRetentionPurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=1, max_length=64)


__all__ = [
    "WorkflowRetentionExportRead",
    "WorkflowRetentionPreviewRead",
    "WorkflowRetentionPurgeRequest",
]
