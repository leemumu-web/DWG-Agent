"""Atomic production-project creation contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.modules.projects.interface import ProjectCreate, ProjectRead
from app.modules.workflows.schemas.orchestration import WorkflowDetail


class ProductionProjectCreate(ProjectCreate):
    """Human-entered project identity for one complete production workflow."""


class ProductionProjectRead(BaseModel):
    project: ProjectRead
    workflow: WorkflowDetail


class ProductionProjectResponseMeta(BaseModel):
    request_id: str
    timestamp: datetime


class ProductionProjectEnvelope(BaseModel):
    data: ProductionProjectRead
    meta: ProductionProjectResponseMeta
