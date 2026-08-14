"""Attempt-aware Job binding and result projection into workflow stages."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.jobs.interface import AnalysisResult, Job
from app.modules.workflows.artifacts import attach_artifact
from app.modules.workflows.contracts import require_stage_outputs
from app.modules.workflows.lifecycle import WORKFLOW_TERMINAL, recompute_workflow
from app.modules.workflows.models import WorkflowArtifact, WorkflowRun
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
    """把 Job 状态重放到工作流投影中（只读重放）。

    投影规则（调用方先用 ``workflow_needs_sync`` 判断是否需要同步；本函数
    不自行决定是否同步）：

    - 绑定的 Job 缺失、或 ``job.attempt != stage.job_attempt`` 时跳过该
      阶段——旧世代数据绝不进入投影。
    - Job 成功时，只投影 ``result_json["job_attempt"]`` 等于当前 attempt
      的 Result（AnalysisResult 没有 attempt 列）；Result 未声明产物类型时
      回退到阶段 capability 的第一个类型。
    - ``drawing_processing`` 额外从拆板账本记录同一 (job_id, attempt) 的
      拆板结果。
    - 必需产物缺失时，阶段置为 ``failed``
      （WORKFLOW_STAGE_OUTPUT_INCOMPLETE），不推进。
    - ``excel_process``→``waiting_review`` 是遗留 excel_delivery 模板的
      人机交接特例；其余模板一律推进到 ``waiting_input``。
    """
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


def workflow_needs_sync(db: Session, workflow: WorkflowRun) -> bool:
    """Read-only drift check: does any stage mirror lag its bound Job?

    `sync_workflow_from_jobs` replays the whole projection and commits; read
    endpoints should only run it when this check reports drift, instead of
    paying the N+1 replay and a write transaction on every poll. This check
    itself must not trigger lazy loads: `workflow.artifacts` is expected to be
    loaded via `load_workflow_detail`'s selectinload, so it is filtered
    in-memory rather than touching `stage.artifacts`.
    """
    artifacts_by_stage: dict[int, list[WorkflowArtifact]] = {}
    for artifact in workflow.artifacts:
        artifacts_by_stage.setdefault(artifact.stage_run_id, []).append(artifact)

    # Two bulk queries for all bound jobs instead of a per-stage N+1.
    # `AnalysisResult` has no attempt column (attempt lives in `result_json`),
    # so the count includes stale attempts from retried jobs; that over-reports
    # drift, which is safe (falls back to the legacy every-poll sync) rather
    # than under-reporting it.
    job_ids = [stage.job_id for stage in workflow.stages if stage.job_id is not None]
    jobs = {
        job.id: job for job in db.scalars(
            select(Job).where(Job.id.in_(job_ids))
        ).all()
    } if job_ids else {}
    result_counts = dict(
        db.execute(
            select(AnalysisResult.job_id, func.count())
            .where(
                AnalysisResult.job_id.in_(job_ids),
                AnalysisResult.status == "succeeded",
            )
            .group_by(AnalysisResult.job_id)
        ).all()
    ) if job_ids else {}

    for stage in workflow.stages:
        if stage.job_id is None:
            continue
        job = jobs.get(stage.job_id)
        if job is None or job.attempt != stage.job_attempt:
            continue
        if (
            stage.status != job.status
            or stage.progress != job.progress
            or stage.error_code != job.error_code
            or stage.error_message != job.error_message
            or (job.started_at is not None and stage.started_at != job.started_at)
            or stage.finished_at != job.finished_at
        ):
            return True
        if job.status != "succeeded":
            continue
        stage_artifacts = [
            artifact
            for artifact in artifacts_by_stage.get(stage.id, [])
            if isinstance(artifact.metadata_json, dict)
            and artifact.metadata_json.get("job_id") == stage.job_id
            and artifact.metadata_json.get("job_attempt") == stage.job_attempt
        ]
        result_count = result_counts.get(job.id, 0)
        if result_count and len(stage_artifacts) < result_count:
            return True
        # `sync` also writes drawing_processing output_json when the split run
        # reaches a terminal outcome; without this check a run that finished
        # after the last sync would never be projected.
        if stage.stage_code == "drawing_processing" and stage.output_json is None:
            return True
        # A succeeded job also advances the next pending stage in sync.
        next_stage = _next_stage(workflow, stage.sequence)
        if next_stage is not None and next_stage.status == "pending":
            return True
    return False
