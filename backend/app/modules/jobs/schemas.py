from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Reserved / dangerous param key prefixes — blocks prototype-pollution vectors
# (__proto__, constructor, $where, etc.) even though Python backends are not
# directly vulnerable; params may be forwarded to front-ends or analytics.
_FORBIDDEN_PARAM_KEY_RE = re.compile(r"^(\$|__|constructor$)")


class JobCreate(BaseModel):
    drawing_id: int | None = None
    project_id: int | None = None
    task_type: str = Field(
        default="framework_smoke_test",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]+$",
        description="Task type — lowercase snake_case identifier.",
    )
    precision_level: str = Field(default="normal", min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _reject_dangerous_keys(cls, v: dict[str, Any]) -> dict[str, Any]:
        for key in v:
            if _FORBIDDEN_PARAM_KEY_RE.search(key):
                raise ValueError(f"Param key {key!r} is not allowed.")
        return v


class ConversionBatchCreate(BaseModel):
    task_type: Literal["convert_dwg_to_dxf", "convert_dxf_to_dwg"]
    file_ids: list[int] = Field(min_length=1, max_length=200)
    precision_level: str = Field(default="normal", min_length=1, max_length=32)


class JobBulkCancellation(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=200)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int | None = None
    drawing_id: int | None = None
    created_by: int | None = None
    task_type: str
    precision_level: str
    pipeline: str | None = None
    status: str
    attempt: int
    priority: int
    progress: int
    params_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    progress_data: dict[str, Any] | None = None
    result_available: bool | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_id: int
    attempt: int
    step_name: str
    worker_name: str | None = None
    status: str
    input_json: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AnalysisResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_id: int
    drawing_id: int | None = None
    result_type: str
    result_json: dict[str, Any] | None = None
    confidence: Decimal | None = None
    result_file_id: int | None = None
    algorithm_version: str | None = None
    tool_version: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class ReviewCreate(BaseModel):
    decision: Literal["approved", "rejected", "needs_revision"]
    comment: str | None = None


class ReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    result_id: int
    reviewer_id: int | None = None
    decision: str
    comment: str | None = None
    created_at: datetime
