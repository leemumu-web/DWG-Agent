from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.files.interface import FileRead
from app.modules.jobs.interface import JobRead


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


class DxfClassificationGroupRead(BaseModel):
    group_key: str
    label: str
    part_type: str | None
    type_source: str | None
    disposition: str
    count: int
    warning_count: int
    total_size_bytes: int


class DxfClassificationGroupItemRead(BaseModel):
    output_name: str
    part_type: str | None
    profile_raw: str | None
    profile_normalized: str | None
    type_source: str | None
    disposition: str
    diagnostics: list[str]
    size_bytes: int


class DxfClassificationGroupPage(BaseModel):
    items: list[DxfClassificationGroupItemRead]
    total: int
    page: int
    page_size: int


class DxfNextStageInput(BaseModel):
    classification_item_id: int
    drawing_id: int | None
    part_type: str
    profile_normalized: str | None
    type_source: str
    source_file_id: int
    output_file_id: int
    classifier_version: str


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
    groups: list[DxfClassificationGroupRead]
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
