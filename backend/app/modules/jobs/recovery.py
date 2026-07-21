"""Authoritative Job summaries and stale-worker recovery."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.modules.excel_processing.interface import cleanup_excel_processing_rows
from app.modules.jobs.models import Job
from app.platform.config.constants import JOB_FAILED, JOB_RUNNING
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal

logger = logging.getLogger(__name__)


def summarize_job_execution(
    job_id: int,
    pipeline: str,
    *,
    session_factory: sessionmaker | None = None,
) -> dict[str, int | str]:
    """Build a Celery return payload from the authoritative MySQL Job row."""
    factory = session_factory or SessionLocal
    with factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            return {
                "job_id": job_id,
                "pipeline": pipeline,
                "status": "missing",
                "attempt": 0,
            }
        return {
            "job_id": job.id,
            "pipeline": pipeline,
            "status": job.status,
            "attempt": job.attempt,
        }


def reconcile_stale_running_jobs(
    session_factory: sessionmaker = SessionLocal,
    *,
    timeout_seconds: int | None = None,
) -> int:
    """Fail running attempts abandoned by a dead SQL-transport worker.

    A second conditional update protects attempts that complete or report new
    progress after the candidate scan. This is a recovery boundary, not a
    broker lease or the target fencing-token model.
    """
    timeout = timeout_seconds or settings.celery_stale_job_timeout_seconds
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=timeout)
    recovered = 0
    with session_factory() as db:
        candidates = db.execute(
            select(Job.id, Job.progress, Job.attempt).where(
                Job.status == JOB_RUNNING,
                Job.updated_at < cutoff,
            )
        ).all()
        for job_id, progress, attempt in candidates:
            message = (
                f"Worker stopped updating this job for more than {timeout} seconds. "
                "Retry the job after verifying the queue worker is healthy."
            )
            event = {
                "type": "error",
                "status": JOB_FAILED,
                "progress": progress or 0,
                "error_code": "CELERY_WORKER_LOST",
                "error_message": message,
                "message": message,
                "job_id": job_id,
                "attempt": attempt,
            }
            result = db.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JOB_RUNNING,
                    Job.attempt == attempt,
                    Job.updated_at < cutoff,
                )
                .values(
                    status=JOB_FAILED,
                    error_code="CELERY_WORKER_LOST",
                    error_message=message,
                    progress_data=event,
                    finished_at=now,
                    updated_at=now,
                )
            )
            updated = result.rowcount or 0
            if updated:
                cleanup_excel_processing_rows(db, (job_id,))
            recovered += updated
        db.commit()
    return recovered


def _reconcile_jobs_on_worker_ready() -> None:
    recovered = reconcile_stale_running_jobs()
    if recovered:
        logger.warning("Marked %s stale running jobs as failed", recovered)


def register_job_worker_maintenance() -> None:
    """Register domain recovery in the generic Celery worker-ready seam."""
    from app.platform.messaging.celery_app import register_worker_ready_callback

    register_worker_ready_callback(
        "jobs.reconcile_stale_running",
        _reconcile_jobs_on_worker_ready,
    )
