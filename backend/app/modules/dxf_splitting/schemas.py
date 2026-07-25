from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.files.interface import FileRead
from app.modules.jobs.interface import JobRead


class DxfSplitItemRead(BaseModel):
    id: int
    drawing_id: int | None
    classification_item_id: int
    source_file_id: int
    source_name: str
    part_type: str
    profile_normalized: str | None
    family: str | None
    source_contract_id: str | None
    automation_route: str
    disposition: str
    normal_dxf_file_id: int | None
    weld_allowance_dxf_file_id: int | None
    split_report_file_id: int | None
    weld_allowance_report_file_id: int | None
    diagnostics: list[str]
    validation: dict[str, object]


class DxfSplitRunRead(BaseModel):
    id: int
    workflow_run_id: int
    status: str
    splitter_version: str
    cli_schema: str | None
    validation_schema: str | None
    input_manifest_sha256: str
    input_count: int
    auto_accepted_count: int
    manual_review_count: int
    source_contracts: dict[str, str]
    bh_split_ledger_file: FileRead | None
    split_manifest_file: FileRead | None
    validation_report_file: FileRead | None
    job: JobRead
    items: list[DxfSplitItemRead]
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DxfSplitHandoffDrawing(BaseModel):
    drawing_id: int | None
    classification_item_id: int
    source_file_id: int
    normal_dxf_file_id: int
    weld_allowance_dxf_file_id: int
    part_type: str


class DxfSplitExcelHandoff(BaseModel):
    workflow_id: int
    split_run_id: int
    job_attempt: int
    input_manifest_sha256: str
    bh_split_ledger_file_id: int
    drawings: list[DxfSplitHandoffDrawing]
