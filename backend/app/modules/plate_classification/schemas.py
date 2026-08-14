"""Pydantic schemas for plate classification API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PlateClassificationTriggerRequest(BaseModel):
    """触发分类请求。"""

    workflow_run_id: int
    project_id: int
    project_name: str
    input_directory: str


class PlateClassificationItemRead(BaseModel):
    """单板分类结果。"""

    id: int
    part_name: str
    dxf_file: str
    category: str
    shape: str
    hole: str
    bend: str


class PlateClassificationRunRead(BaseModel):
    """分类运行摘要。"""

    id: int
    workflow_run_id: int
    status: str
    classifier_version: str
    project_name: str
    input_directory: str
    input_count: int
    classified_count: int
    category_counts: dict[str, int] | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[PlateClassificationItemRead]


class PlateClassificationRunPage(BaseModel):
    """分类运行分页。"""

    items: list[PlateClassificationRunRead]
    total: int
    page: int
    page_size: int
