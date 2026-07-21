"""Shared workflow loading and authorization constants for route adapters."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.workflows.lifecycle import get_workflow_or_404
from app.modules.workflows.models import WorkflowRun

WORKFLOW_WRITE_ROLES = {"project_owner", "project_engineer"}
WORKFLOW_STATUSES = {
    "draft",
    "waiting_input",
    "running",
    "waiting_review",
    "succeeded",
    "failed",
    "cancelled",
}


def load_workflow_detail(db: Session, workflow_id: int) -> WorkflowRun:
    workflow = db.scalar(
        select(WorkflowRun)
        .where(WorkflowRun.id == workflow_id)
        .options(selectinload(WorkflowRun.stages), selectinload(WorkflowRun.artifacts))
    )
    if workflow is None:
        return get_workflow_or_404(db, workflow_id)
    return workflow
