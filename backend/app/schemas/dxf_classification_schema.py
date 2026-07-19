from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.file_schema import FileRead
from app.schemas.job_schema import JobRead


class DxfClassificationItemRead(BaseModel):
    id: int
    drawing_id: int | None
    source_file: FileRead
    output_file: FileRead
    source_name: str
    output_name: str
    output_directory: str
    disposition: str
    part_type: str | None
    diagnostics: list[str]


class DxfClassificationRunRead(BaseModel):
    id: int
    workflow_run_id: int
    status: str
    classifier_version: str
    report_schema: str | None
    cli_schema: str | None
    project_name: str
    input_manifest_sha256: str
    input_count: int
    classified_count: int
    review_required_count: int
    unreadable_count: int
    type_counts: dict[str, int]
    report_file: FileRead | None
    manifest_file: FileRead | None
    job: JobRead
    items: list[DxfClassificationItemRead]
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
