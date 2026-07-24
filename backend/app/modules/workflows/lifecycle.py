"""Workflow state-machine transitions independent from HTTP transport."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.workflows.models import WorkflowRun, WorkflowStageRun
from app.modules.workflows.schemas import WorkflowCreate
from app.modules.workflows.templates import WORKFLOW_DEFINITIONS, get_stage_capability
from app.platform.http.exceptions import AppHTTPException, not_found

WORKFLOW_TERMINAL = {"succeeded", "failed", "cancelled"}
STAGE_TERMINAL = {"succeeded", "failed", "cancelled", "skipped"}
STAGE_ACTIVE = {"queued", "running"}


def create_workflow(db: Session, payload: WorkflowCreate, *, created_by: int) -> WorkflowRun:
    config = dict(payload.config)
    if payload.workflow_type == "linux_production":
        config["definition_revision"] = 2
    workflow = WorkflowRun(
        project_id=payload.project_id,
        created_by=created_by,
        name=payload.name,
        workflow_type=payload.workflow_type,
        status="draft",
        progress=0,
        config_json=config,
    )
    db.add(workflow)
    db.flush()
    for sequence, (stage_code, name) in enumerate(
        WORKFLOW_DEFINITIONS[payload.workflow_type], start=1
    ):
        db.add(
            WorkflowStageRun(
                workflow_run_id=workflow.id,
                stage_code=stage_code,
                name=name,
                sequence=sequence,
                status="ready" if sequence == 1 else "pending",
                progress=0,
            )
        )
    db.flush()
    return workflow


def get_workflow_or_404(db: Session, workflow_id: int) -> WorkflowRun:
    workflow = db.scalar(select(WorkflowRun).where(WorkflowRun.id == workflow_id))
    if workflow is None:
        raise not_found("Workflow")
    return workflow


def start_workflow(db: Session, workflow: WorkflowRun) -> WorkflowRun:
    if workflow.status != "draft":
        raise AppHTTPException(409, "WORKFLOW_NOT_DRAFT", "Only a draft workflow can start.")
    first = min(workflow.stages, key=lambda stage: stage.sequence)
    now = datetime.now(UTC)
    workflow.status = "waiting_input"
    workflow.current_stage = first.stage_code
    workflow.started_at = now
    first.status = "waiting_input"
    first.started_at = now
    return workflow


def complete_manual_stage(workflow: WorkflowRun, stage_code: str) -> WorkflowRun:
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    if stage.status not in {"ready", "waiting_input", "waiting_review"}:
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_NOT_ACTIONABLE",
            "This workflow stage is not awaiting input.",
        )
    capability = get_stage_capability(workflow, stage_code)
    if capability.execution_mode == "automated":
        raise AppHTTPException(
            409,
            "WORKFLOW_STAGE_REQUIRES_EXECUTION",
            "This automated stage must use its execution endpoint.",
        )
    if (
        workflow.workflow_type == "linux_production"
        and stage_code == "source_intake"
        and (workflow.input_batch is None or workflow.input_batch.status != "frozen")
    ):
        raise AppHTTPException(
            409,
            "WORKFLOW_INPUT_BATCH_NOT_FROZEN",
            "The production input batch must be validated and frozen through its dedicated endpoint.",
        )
    if capability.execution_mode in {"placeholder", "external"} and not stage.artifacts:
        raise AppHTTPException(
            409,
            "WORKFLOW_HANDOFF_ARTIFACT_REQUIRED",
            "At least one handoff artifact must be bound before confirming this stage.",
        )
    now = datetime.now(UTC)
    stage.status = "succeeded"
    stage.progress = 100
    stage.finished_at = now
    next_stage = _next_stage(workflow, stage.sequence)
    if next_stage is not None and next_stage.status == "pending":
        next_stage.status = "waiting_input"
        next_stage.started_at = now
    recompute_workflow(workflow)
    return workflow


def cancel_workflow(workflow: WorkflowRun) -> WorkflowRun:
    if workflow.status in WORKFLOW_TERMINAL:
        raise AppHTTPException(409, "WORKFLOW_TERMINAL", "Workflow is already terminal.")
    now = datetime.now(UTC)
    workflow.status = "cancelled"
    workflow.finished_at = now
    for stage in workflow.stages:
        if stage.status not in STAGE_TERMINAL:
            stage.status = "cancelled"
            stage.finished_at = now
    return workflow


def recompute_workflow(workflow: WorkflowRun) -> None:
    stages = sorted(workflow.stages, key=lambda item: item.sequence)
    if not stages:
        workflow.progress = 0
        return
    workflow.progress = round(sum(stage.progress for stage in stages) / len(stages))
    if workflow.status == "cancelled":
        return
    failed = next((stage for stage in stages if stage.status == "failed"), None)
    if failed is not None:
        workflow.status = "failed"
        workflow.current_stage = failed.stage_code
        workflow.error_code = failed.error_code
        workflow.error_message = failed.error_message
        workflow.finished_at = failed.finished_at or datetime.now(UTC)
        return
    cancelled = next((stage for stage in stages if stage.status == "cancelled"), None)
    if cancelled is not None:
        workflow.status = "failed"
        workflow.current_stage = cancelled.stage_code
        workflow.error_code = "WORKFLOW_STAGE_CANCELLED"
        workflow.error_message = "The current stage job was cancelled and can be retried."
        workflow.finished_at = cancelled.finished_at or datetime.now(UTC)
        return
    if all(stage.status in {"succeeded", "skipped"} for stage in stages):
        workflow.status = "succeeded"
        workflow.progress = 100
        workflow.current_stage = stages[-1].stage_code
        workflow.finished_at = datetime.now(UTC)
        return
    current = next((stage for stage in stages if stage.status not in STAGE_TERMINAL), stages[-1])
    workflow.current_stage = current.stage_code
    if current.status in STAGE_ACTIVE:
        workflow.status = "running"
    elif current.status == "waiting_review":
        workflow.status = "waiting_review"
    elif current.status in {"ready", "waiting_input"}:
        workflow.status = "waiting_input"


def _next_stage(workflow: WorkflowRun, sequence: int) -> WorkflowStageRun | None:
    return next((stage for stage in workflow.stages if stage.sequence == sequence + 1), None)
