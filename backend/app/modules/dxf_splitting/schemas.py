from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    processed_count: int
    failed_count: int
    reviewed_count: int
    elapsed_seconds: int
    throughput_per_minute: float | None
    estimated_remaining_seconds: int | None
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


class DxfSplitReviewDecisionWrite(BaseModel):
    decision: Literal["accept_candidate", "manual_processing"]
    comment: str = Field(min_length=2, max_length=1000)
    expected_version: int = Field(ge=0)

    @field_validator("comment")
    @classmethod
    def validate_comment(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("人工复核说明至少需要两个字符。")
        return normalized


class DxfSplitReviewDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    split_item_id: int
    decision: Literal["accept_candidate", "manual_processing"]
    final_normal_dxf_file_id: int | None
    final_weld_allowance_dxf_file_id: int | None
    comment: str
    decided_by: int
    decided_at: datetime
    version: int


class DxfSplitReviewItemRead(BaseModel):
    id: int
    source_name: str
    part_type: str
    profile_normalized: str | None
    disposition: str
    diagnostics: list[str]
    candidate_available: bool
    decision: DxfSplitReviewDecisionRead | None


class DxfSplitReviewPage(BaseModel):
    items: list[DxfSplitReviewItemRead]
    total: int
    page: int
    page_size: int
    pending_count: int
    manual_processing_count: int


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
