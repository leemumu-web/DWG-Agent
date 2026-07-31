"""Executable framework smoke worker; production analysis remains a placeholder."""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from app.modules.files.interface import save_bytes_as_file
from app.modules.jobs.event_stream import make_event
from app.modules.jobs.lifecycle import (
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    fail_job_attempt,
)
from app.modules.jobs.models import AnalysisResult, JobStep
from app.platform.config.constants import JOB_RUNNING, JOB_SUCCEEDED, PIPELINE_STUB
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.time import business_now


def _exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message:
            return message
    message = str(exc)
    return message or exc.__class__.__name__


def _mark_job_failed(db: Session, job_id: int, attempt: int, exc: Exception) -> None:
    fail_job_attempt(
        db,
        job_id,
        attempt=attempt,
        error_code="STUB_WORKER_FAILED",
        error_message=_exception_message(exc),
    )


def run_local_stub_job(
    job_id: int,
    worker_name: str = "celery_stub",
    expected_attempt: int = 1,
) -> None:
    """Celery fake task for Stage 1: prove queue/status/result/review plumbing."""
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_STUB,
            progress=20,
            message="任务已接收",
        )
        if job is None:
            return
        attempt = job.attempt
        started_at = job.started_at or business_now()
        db.add(
            JobStep(
                job_id=job.id,
                attempt=attempt,
                step_name="dispatch_stub_worker",
                worker_name=worker_name,
                status="succeeded",
                input_json={"pipeline": PIPELINE_STUB},
                output_json={"message": "Celery framework stub accepted the job."},
                started_at=started_at,
                finished_at=business_now(),
            )
        )
        job = commit_job_progress(
            db,
            job.id,
            attempt=attempt,
            progress=20,
            event=make_event(
                type_="progress",
                status=JOB_RUNNING,
                progress=20,
                message="Celery framework stub accepted the job.",
            ),
        )
        if job is None:
            return

        result_payload = {
            "source": "local_stub",
            "job_id": job.id,
            "task_type": job.task_type,
            "precision_level": job.precision_level,
            "message": "Agent、DWG/DXF 与 CAD Worker 尚未接入；当前结果用于验证任务、结果、下载、审计链路。",
        }
        bucket = settings.minio_bucket_derived
        storage_key = f"jobs/{job.id}/{uuid4().hex}.json"
        raw = json.dumps(result_payload, ensure_ascii=False, indent=2).encode("utf-8")

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
                attempt=attempt,
                step_name="write_stub_result",
                worker_name=worker_name,
                status="succeeded",
                input_json={"result_file_id": result_file.id},
                output_json={"analysis_result": "created"},
                started_at=business_now(),
                finished_at=business_now(),
            )
        )
        complete_job_attempt(
            db,
            job.id,
            attempt=attempt,
            event=make_event(
                type_="done", status=JOB_SUCCEEDED, progress=100, message="任务已完成"
            ),
        )
    except Exception as exc:
        db.rollback()
        if "attempt" in locals():
            _mark_job_failed(db, job_id, attempt, exc)
        raise
    finally:
        db.close()
