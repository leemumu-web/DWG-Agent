from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


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
