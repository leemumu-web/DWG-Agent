"""Shared workflow loading and authorization constants for route adapters."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.modules.jobs.interface import AnalysisResult
from app.modules.workflows.lifecycle import get_workflow_or_404
from app.modules.workflows.models import (
    WorkflowArtifact,
    WorkflowInputBatch,
    WorkflowInputItem,
    WorkflowRun,
)

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


def load_workflow_detail(
    db: Session,
    workflow_id: int,
    *,
    for_update: bool = False,
) -> WorkflowRun:
    statement = (
        select(WorkflowRun)
        .where(WorkflowRun.id == workflow_id)
        .options(selectinload(WorkflowRun.stages), selectinload(WorkflowRun.artifacts))
    )
    if for_update:
        statement = statement.with_for_update()
    workflow = db.scalar(statement)
    if workflow is None:
        return get_workflow_or_404(db, workflow_id)
    return workflow


def find_production_file_workflow_id(db: Session, file_id: int) -> int | None:
    """Return the production workflow that owns a file's download boundary."""
    workflow_id = db.scalar(
        select(WorkflowArtifact.workflow_run_id)
        .join(WorkflowRun, WorkflowRun.id == WorkflowArtifact.workflow_run_id)
        .outerjoin(AnalysisResult, AnalysisResult.id == WorkflowArtifact.result_id)
        .where(
            or_(
                WorkflowArtifact.file_id == file_id,
                AnalysisResult.result_file_id == file_id,
            ),
            WorkflowRun.workflow_type == "linux_production",
        )
        .limit(1)
    )
    if workflow_id is not None:
        return workflow_id
    workflow_id = db.scalar(
        select(WorkflowInputBatch.workflow_run_id)
        .join(WorkflowInputItem, WorkflowInputItem.input_batch_id == WorkflowInputBatch.id)
        .join(WorkflowRun, WorkflowRun.id == WorkflowInputBatch.workflow_run_id)
        .where(
            or_(
                WorkflowInputItem.file_id == file_id,
                WorkflowInputItem.derived_dxf_file_id == file_id,
            ),
            WorkflowRun.workflow_type == "linux_production",
        )
        .limit(1)
    )
    if workflow_id is not None:
        return workflow_id
    # Split candidates are deliberately not formal workflow artifacts until
    # review acceptance, but they remain production files and ZIP-only.
    from app.modules.dxf_splitting.interface import find_dxf_split_file_workflow_id

    return find_dxf_split_file_workflow_id(db, file_id)
