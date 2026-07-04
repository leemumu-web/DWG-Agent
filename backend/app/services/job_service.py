from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import (
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PIPELINE_STUB,
)
from app.core.exceptions import AppHTTPException
from app.db.session import SessionLocal
from app.models.drawing import Drawing
from app.models.job import Job, JobStep
from app.models.result import AnalysisResult
from app.schemas.job_schema import JobCreate
from app.services.storage_service import save_bytes_as_file


def create_job(db: Session, payload: JobCreate, created_by: int | None) -> Job:
    project_id = payload.project_id
    if project_id is None and payload.drawing_id is not None:
        drawing = db.get(Drawing, payload.drawing_id)
        if drawing:
            project_id = drawing.project_id
    job = Job(
        project_id=project_id,
        drawing_id=payload.drawing_id,
        created_by=created_by,
        task_type=payload.task_type,
        precision_level=payload.precision_level,
        pipeline=PIPELINE_STUB,
        status=JOB_QUEUED,
        progress=0,
        params_json=payload.params,
    )
    db.add(job)
    db.flush()
    return job


def enqueue_stub_job(job_id: int) -> str:
    from app.workers.tasks_report import run_stub_job_task

    async_result = run_stub_job_task.delay(job_id)
    return str(async_result.id)


def _exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message:
            return message
    message = str(exc)
    return message or exc.__class__.__name__


def _mark_job_failed(job_id: int, exc: Exception) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job and job.status not in (JOB_SUCCEEDED, JOB_CANCELLED):
            job.status = JOB_FAILED
            job.error_code = "STUB_WORKER_FAILED"
            job.error_message = _exception_message(exc)
            job.finished_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()


def run_local_stub_job(job_id: int, worker_name: str = "celery_stub") -> None:
    """Celery fake task for Stage 1: prove queue/status/result/review plumbing."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            return
        if job.status != JOB_QUEUED:
            return
        started_at = datetime.now(UTC)
        job.status = JOB_RUNNING
        job.progress = 20
        job.started_at = started_at
        db.add(
            JobStep(
                job_id=job.id,
                step_name="dispatch_stub_worker",
                worker_name=worker_name,
                status="succeeded",
                input_json={"pipeline": PIPELINE_STUB},
                output_json={"message": "Celery framework stub accepted the job."},
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()

        result_payload = {
            "source": "local_stub",
            "job_id": job.id,
            "task_type": job.task_type,
            "precision_level": job.precision_level,
            "message": "Agent、DWG/DXF 与 CAD Worker 尚未接入；当前结果用于验证任务、结果、下载、审计链路。",
        }
        bucket = "dwg-derived"
        storage_key = f"jobs/{job.id}/{uuid4().hex}.json"
        raw = json.dumps(result_payload, ensure_ascii=False, indent=2).encode("utf-8")

        job = db.scalars(select(Job).where(Job.id == job_id).with_for_update()).one_or_none()
        if not job or job.status != JOB_RUNNING:
            db.rollback()
            return

        result_file = save_bytes_as_file(
            db,
            bucket=bucket,
            storage_key=storage_key,
            original_name=f"job-{job.id}-result.json",
            file_ext=".json",
            content_type="application/json",
            payload=raw,
            uploaded_by=job.created_by,
        )

        result = AnalysisResult(
            job_id=job.id,
            drawing_id=job.drawing_id,
            result_type=job.task_type,
            result_json=result_payload,
            confidence=Decimal("1.0000"),
            result_file_id=result_file.id,
            algorithm_version="framework-stub-v0.1",
            tool_version="local-stub",
            status="succeeded",
        )
        db.add(result)
        db.add(
            JobStep(
                job_id=job.id,
                step_name="write_stub_result",
                worker_name=worker_name,
                status="succeeded",
                input_json={"result_file_id": result_file.id},
                output_json={"analysis_result": "created"},
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
        )
        job.status = JOB_SUCCEEDED
        job.progress = 100
        job.finished_at = datetime.now(UTC)
        db.commit()
    except Exception as exc:
        db.rollback()
        _mark_job_failed(job_id, exc)
        raise
    finally:
        db.close()


def cancel_job(db: Session, job: Job) -> Job:
    """Cancel a job. Raises 409 if job is already in a terminal state."""
    if job.status in ("succeeded", "failed", "cancelled"):
        raise AppHTTPException(
            409,
            "JOB_NOT_CANCELLABLE",
            f"Job cannot be cancelled because it is already {job.status}.",
        )
    job.status = JOB_CANCELLED
    return job


def retry_job(db: Session, job: Job) -> Job:
    """Retry a failed or cancelled job. Raises 409 if job is not retryable."""
    if job.status not in ("failed", "cancelled"):
        raise AppHTTPException(
            409,
            "JOB_NOT_RETRYABLE",
            f"Job cannot be retried because it is {job.status}. Only failed or cancelled jobs can be retried.",
        )
    job.status = JOB_QUEUED
    job.progress = 0
    return job
