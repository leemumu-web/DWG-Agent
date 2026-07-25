"""HTTP contracts for selective workflow batch export and permanent cleanup."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

WorkflowExportCategory = Literal[
    "classified_dxf",
    "processed_dxf",
    "source_excel",
    "stage1_excel",
    "split_result_normal",
    "split_result_allowance",
]

DrawingSelectiveExportCategory = Literal[
    "failed_bh",
    "failed_box",
    "pl",
    "other",
]


class WorkflowBatchExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[WorkflowExportCategory] = Field(min_length=1, max_length=6)

    @field_validator("categories")
    @classmethod
    def categories_must_be_unique(
        cls,
        value: list[WorkflowExportCategory],
    ) -> list[WorkflowExportCategory]:
        if len(value) != len(set(value)):
            raise ValueError("导出类别不能重复")
        return value


class WorkflowBatchExportCategoryRead(BaseModel):
    key: WorkflowExportCategory
    label: str
    file_count: int
    size_bytes: int
    available: bool


class WorkflowBatchExportPreviewRead(BaseModel):
    workflow_id: int
    categories: list[WorkflowBatchExportCategoryRead]


class WorkflowBatchExportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    export_uid: str
    workflow_run_id: int
    status: str
    categories: list[WorkflowExportCategory]
    file_count: int
    source_size_bytes: int
    filename: str
    download_url: str | None = None
    token_expires_at: datetime
    downloaded_at: datetime | None = None
    purged_at: datetime | None = None
    purged_file_count: int = 0
    purged_size_bytes: int = 0
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class WorkflowBatchExportPurgeRead(BaseModel):
    export_uid: str
    status: Literal["purged"]
    purged_file_count: int
    released_bytes: int


class DrawingSelectiveExportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    categories: list[DrawingSelectiveExportCategory] = Field(min_length=1, max_length=4)

    @field_validator("categories")
    @classmethod
    def categories_must_be_unique(
        cls,
        value: list[DrawingSelectiveExportCategory],
    ) -> list[DrawingSelectiveExportCategory]:
        if len(value) != len(set(value)):
            raise ValueError("导出类别不能重复")
        return value


class DrawingSelectiveExportCategoryRead(BaseModel):
    key: DrawingSelectiveExportCategory
    label: str
    file_count: int
    size_bytes: int
    available: bool


class DrawingSelectiveExportPreviewRead(BaseModel):
    workflow_id: int
    split_run_id: int
    categories: list[DrawingSelectiveExportCategoryRead]


class DrawingSelectiveExportRead(BaseModel):
    categories: list[DrawingSelectiveExportCategory]
    file_count: int
    source_size_bytes: int
    filename: str
    download_url: str
    token_expires_at: datetime


__all__ = [
    "DrawingSelectiveExportCategory",
    "DrawingSelectiveExportCategoryRead",
    "DrawingSelectiveExportCreate",
    "DrawingSelectiveExportPreviewRead",
    "DrawingSelectiveExportRead",
    "WorkflowBatchExportCategoryRead",
    "WorkflowBatchExportCreate",
    "WorkflowBatchExportPreviewRead",
    "WorkflowBatchExportPurgeRead",
    "WorkflowBatchExportRead",
    "WorkflowExportCategory",
]
