"""Attempt-aware Job binding and result projection into workflow stages."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.workflows.artifacts import attach_artifact
from app.modules.workflows.contracts import require_stage_outputs
from app.modules.workflows.lifecycle import WORKFLOW_TERMINAL, recompute_workflow
from app.modules.workflows.models import WorkflowRun
from app.modules.workflows.templates import get_stage_capability
from app.platform.http.exceptions import AppHTTPException
from app.platform.time import business_now


def bind_stage_job(db: Session, workflow: WorkflowRun, *, stage_code: str, job: Job) -> None:
    stage = next((item for item in workflow.stages if item.stage_code == stage_code), None)
    if stage is None:
        raise AppHTTPException(422, "WORKFLOW_STAGE_UNKNOWN", "Unknown workflow stage.")
    if workflow.status in WORKFLOW_TERMINAL:
        if workflow.status != "failed" or workflow.current_stage != stage_code:
            raise AppHTTPException(
                409, "WORKFLOW_TERMINAL", "Terminal workflow cannot accept a job."
            )
    stage.job_id = job.id
    stage.job_attempt = job.attempt
    stage.status = job.status
    stage.progress = job.progress
    stage.error_code = None
    stage.error_message = None
    stage.output_json = None
    stage.finished_at = None
    stage.started_at = job.started_at or business_now()
    workflow.current_stage = stage.stage_code
    workflow.status = "running"
    workflow.error_code = None
    workflow.error_message = None
    workflow.finished_at = None
    recompute_workflow(workflow)
    db.flush()


def sync_workflow_from_jobs(db: Session, workflow: WorkflowRun) -> WorkflowRun:
    now = business_now()
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
        if job.status == "succeeded":
            capability = get_stage_capability(workflow, stage.stage_code)
            results = list(
                db.scalars(
                    select(AnalysisResult).where(
                        AnalysisResult.job_id == job.id,
                        AnalysisResult.status == "succeeded",
                    )
                ).all()
            )
            results = [
                result
                for result in results
                if isinstance(result.result_json, dict)
                and result.result_json.get("job_attempt") == job.attempt
            ]
            for result in results:
                requested_artifact_type = (
                    result.result_json.get("workflow_artifact_type")
                    if isinstance(result.result_json, dict)
                    else None
                )
                artifact_type = (
                    requested_artifact_type
                    if isinstance(requested_artifact_type, str)
                    and requested_artifact_type in capability.artifact_types
                    else capability.artifact_types[0]
                    if capability.artifact_types
                    else f"{stage.stage_code}_result"
                )
                attach_artifact(
                    db,
                    workflow,
                    stage_code=stage.stage_code,
                    artifact_type=artifact_type,
                    file_id=result.result_file_id,
                    result_id=result.id,
                    metadata={"job_id": job.id, "job_attempt": job.attempt},
                )
            if stage.stage_code == "drawing_processing":
                from app.modules.dxf_splitting.interface import get_dxf_split_outcome

                split_outcome = get_dxf_split_outcome(
                    db,
                    job_id=job.id,
                    attempt=job.attempt,
                )
                if split_outcome in {"completed", "completed_with_review"}:
                    stage.output_json = {
                        "split_status": split_outcome,
                        "job_id": job.id,
                        "job_attempt": job.attempt,
                    }
            try:
                require_stage_outputs(workflow, stage.stage_code)
            except AppHTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                stage.status = "failed"
                stage.error_code = str(
                    detail.get("code") or "WORKFLOW_STAGE_OUTPUT_INCOMPLETE"
                )
                stage.error_message = str(
                    detail.get("message") or "Required workflow output is missing."
                )
                stage.finished_at = now
                continue
            if stage.stage_code == "dxf_classification" and _skip_empty_split_stage(
                db,
                workflow,
                classification_job=job,
                now=now,
            ):
                continue
            next_stage = _next_stage(workflow, stage.sequence)
            if next_stage is not None and next_stage.status == "pending":
                next_stage.status = (
                    "waiting_review" if stage.stage_code == "excel_process" else "waiting_input"
                )
                next_stage.started_at = now
    recompute_workflow(workflow)
    db.flush()
    return workflow


def _skip_empty_split_stage(
    db: Session,
    workflow: WorkflowRun,
    *,
    classification_job: Job,
    now: datetime,
) -> bool:
    """Complete the split stage as an explicit no-op when BH/BOX input is empty."""
    from app.modules.dxf_classification.interface import (
        latest_classification_run,
        list_split_candidate_inputs,
    )

    classification = latest_classification_run(db, workflow.id)
    if (
        classification is None
        or classification.job_id != classification_job.id
        or classification.job_attempt != classification_job.attempt
        or classification.status not in {"completed", "completed_with_review"}
        or list_split_candidate_inputs(db, workflow.id)
    ):
        return False
    classification_stage = next(
        (
            stage
            for stage in workflow.stages
            if stage.stage_code == "dxf_classification"
        ),
        None,
    )
    drawing_stage = (
        _next_stage(workflow, classification_stage.sequence)
        if classification_stage is not None
        else None
    )
    if drawing_stage is None or drawing_stage.stage_code != "drawing_processing":
        return False
    drawing_stage.status = "skipped"
    drawing_stage.progress = 100
    drawing_stage.error_code = None
    drawing_stage.error_message = None
    drawing_stage.started_at = drawing_stage.started_at or now
    drawing_stage.finished_at = now
    drawing_stage.output_json = {
        "reason": "no_split_candidates",
        "classification_run_id": classification.id,
        "classification_job_id": classification.job_id,
        "classification_job_attempt": classification.job_attempt,
        "input_manifest_sha256": classification.input_manifest_sha256,
    }
    excel_stage = _next_stage(workflow, drawing_stage.sequence)
    if excel_stage is not None and excel_stage.status == "pending":
        excel_stage.status = "waiting_input"
        excel_stage.started_at = now
    return True


def _next_stage(workflow: WorkflowRun, sequence: int):
    return next((stage for stage in workflow.stages if stage.sequence == sequence + 1), None)
