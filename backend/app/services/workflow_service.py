from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import AppHTTPException, not_found
from app.models.job import Job
from app.models.workflow import WorkflowArtifact, WorkflowRun, WorkflowStageRun
from app.schemas.workflow_schema import WorkflowCreate

WORKFLOW_TERMINAL = {"succeeded", "failed", "cancelled"}
STAGE_TERMINAL = {"succeeded", "failed", "cancelled", "skipped"}
STAGE_ACTIVE = {"queued", "running"}

WORKFLOW_DEFINITIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "excel_delivery": (
        ("source_upload", "上传源 Excel"),
        ("excel_process", "Excel 零件清单处理"),
        ("quality_review", "结果确认"),
        ("delivery", "交付归档"),
    ),
    "file_delivery": (
        ("source_upload", "上传源文件"),
        ("quality_review", "文件确认"),
        ("delivery", "交付归档"),
    ),
}


def create_workflow(db: Session, payload: WorkflowCreate, *, created_by: int) -> WorkflowRun:
    workflow = WorkflowRun(
        project_id=payload.project_id,
        created_by=created_by,
        name=payload.name,
        workflow_type=payload.workflow_type,
        status="draft",
        progress=0,
        config_json=payload.config,
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


def attach_artifact(
    db: Session,
    workflow: WorkflowRun,
    *,
    stage_code: str,
    artifact_type: str,
    file_id: int | None = None,
    result_id: int | None = None,
    metadata: dict | None = None,
) -> WorkflowArtifact:
    if file_id is None and result_id is None:
        raise AppHTTPException(
            422, "WORKFLOW_ARTIFACT_EMPTY", "An artifact must reference a file or result."
        )
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    artifact = WorkflowArtifact(
        workflow_run_id=workflow.id,
        stage_run_id=stage.id,
        artifact_type=artifact_type,
        file_id=file_id,
        result_id=result_id,
        metadata_json=metadata,
    )
    db.add(artifact)
    db.flush()
    return artifact


def bind_stage_job(db: Session, workflow: WorkflowRun, *, stage_code: str, job: Job) -> None:
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    if workflow.status in WORKFLOW_TERMINAL:
        raise AppHTTPException(409, "WORKFLOW_TERMINAL", "Terminal workflow cannot accept a job.")
    stage.job_id = job.id
    stage.job_attempt = job.attempt
    stage.status = job.status
    stage.progress = job.progress
    stage.started_at = job.started_at or datetime.now(UTC)
    workflow.current_stage = stage.stage_code
    workflow.status = "running"
    recompute_workflow(workflow)
    db.flush()


def sync_workflow_from_jobs(db: Session, workflow: WorkflowRun) -> WorkflowRun:
    now = datetime.now(UTC)
    for stage in workflow.stages:
        if stage.job_id is None:
            continue
        job = db.get(Job, stage.job_id)
        if job is None or job.attempt != stage.job_attempt:
            continue
        stage.status = job.status
        stage.progress = job.progress
        stage.error_code = job.error_code
        stage.error_message = job.error_message
        stage.started_at = job.started_at or stage.started_at
        stage.finished_at = job.finished_at
        if job.status == "succeeded" and stage.stage_code == "excel_process":
            next_stage = _next_stage(workflow, stage.sequence)
            if next_stage is not None and next_stage.status == "pending":
                next_stage.status = "waiting_review"
                next_stage.started_at = now
    recompute_workflow(workflow)
    db.flush()
    return workflow


def complete_manual_stage(workflow: WorkflowRun, stage_code: str) -> WorkflowRun:
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    if stage.status not in {"ready", "waiting_input", "waiting_review"}:
        raise AppHTTPException(
            409, "WORKFLOW_STAGE_NOT_ACTIONABLE", "This workflow stage is not awaiting input."
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
    failed = next((stage for stage in stages if stage.status == "failed"), None)
    if failed is not None:
        workflow.status = "failed"
        workflow.current_stage = failed.stage_code
        workflow.error_code = failed.error_code
        workflow.error_message = failed.error_message
        workflow.finished_at = failed.finished_at or datetime.now(UTC)
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
