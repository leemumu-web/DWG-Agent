from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.modules.excel_processing.interface import cleanup_excel_processing_rows
from app.modules.jobs.event_stream import make_event
from app.modules.jobs.models import Job
from app.platform.config.constants import (
    JOB_CANCELLED,
    JOB_FAILED,
    JOB_PENDING,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    JOB_VALIDATING,
    JOB_WAITING_CAD_WORKER,
)
from app.platform.http.exceptions import AppHTTPException

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActiveJobCancellation:
    job_ids: tuple[int, ...]
    cancelled_count: int


def _mysql_error_code(exc: OperationalError) -> int | None:
    args = getattr(exc.orig, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


def execute_guarded_job_update(db: Session, statement):
    """Re-evaluate a conditional job update after MySQL concurrent-change error 1020.

    MySQL can raise 1020 instead of returning zero affected rows when another
    transaction changes the guarded row during an update. After rollback, one
    re-execution is safe only when the status/attempt guard now rejects the row.
    A successful second update is rolled back and the original error is raised
    so callers never commit without their other pending rows.
    """
    try:
        return db.execute(statement)
    except OperationalError as exc:
        if _mysql_error_code(exc) != 1020:
            raise
        db.rollback()
        logger.info("Retrying guarded job update after MySQL concurrent change (1020)")
        result = db.execute(statement)
        if result.rowcount == 0:
            return result
        db.rollback()
        raise exc


def claim_queued_job(
    db: Session,
    job_id: int,
    *,
    expected_attempt: int | None = None,
    pipeline: str,
    progress: int,
    message: str,
    event_data: dict[str, object] | None = None,
) -> Job | None:
    """Atomically transition one queued job to running.

    The conditional UPDATE is the cross-process idempotency boundary. Only the
    worker whose statement updates one row may perform external side effects.
    """
    started_at = datetime.now(UTC)
    event = make_event(
        type_="status",
        status=JOB_RUNNING,
        progress=progress,
        message=message,
        **(event_data or {}),
    )
    event["job_id"] = job_id
    conditions = [Job.id == job_id, Job.status == JOB_QUEUED]
    if expected_attempt is not None:
        conditions.append(Job.attempt == expected_attempt)
        event["attempt"] = expected_attempt
    result = execute_guarded_job_update(
        db,
        update(Job)
        .where(*conditions)
        .values(
            status=JOB_RUNNING,
            progress=progress,
            pipeline=pipeline,
            started_at=started_at,
            progress_data=event,
            updated_at=started_at,
        ),
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id, populate_existing=True)


def commit_job_progress(
    db: Session,
    job_id: int,
    *,
    attempt: int,
    progress: int,
    event: dict[str, object],
) -> Job | None:
    """Commit pending step data and progress only for the active execution attempt."""
    now = datetime.now(UTC)
    payload = dict(event)
    payload.update(
        {
            "job_id": job_id,
            "status": JOB_RUNNING,
            "progress": progress,
            "attempt": attempt,
        }
    )
    result = execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JOB_RUNNING,
            Job.attempt == attempt,
        )
        .values(progress=progress, progress_data=payload, updated_at=now)
        .execution_options(synchronize_session=False),
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id, populate_existing=True)


def complete_job_attempt(
    db: Session,
    job_id: int,
    *,
    attempt: int,
    event: dict[str, object],
) -> Job | None:
    """Commit pending result rows and succeed only the worker's own attempt."""
    finished_at = datetime.now(UTC)
    payload = dict(event)
    payload.update(
        {
            "job_id": job_id,
            "type": "done",
            "status": JOB_SUCCEEDED,
            "progress": 100,
            "attempt": attempt,
        }
    )
    result = execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JOB_RUNNING,
            Job.attempt == attempt,
        )
        .values(
            status=JOB_SUCCEEDED,
            progress=100,
            error_code=None,
            error_message=None,
            progress_data=payload,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        .execution_options(synchronize_session=False),
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id, populate_existing=True)


def fail_job_attempt(
    db: Session,
    job_id: int,
    *,
    attempt: int,
    error_code: str,
    error_message: str,
) -> Job | None:
    """Fail only the execution generation that raised the error."""
    finished_at = datetime.now(UTC)
    payload = {
        "job_id": job_id,
        "type": "error",
        "status": JOB_FAILED,
        "attempt": attempt,
        "error_code": error_code,
        "error_message": error_message,
        "message": error_message,
    }
    result = execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JOB_RUNNING,
            Job.attempt == attempt,
        )
        .values(
            status=JOB_FAILED,
            error_code=error_code,
            error_message=error_message,
            progress_data=payload,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        .execution_options(synchronize_session=False),
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(Job, job_id, populate_existing=True)


def cancel_active_jobs_in_transaction(db: Session) -> ActiveJobCancellation:
    """Lock and cancel the exact administrator-visible active Job set.

    The caller owns audit persistence and the commit. Queue purging happens only
    after that commit because the SQL transport is a separate system.
    """
    active_statuses = (JOB_QUEUED, JOB_RUNNING, JOB_PENDING)
    job_ids = tuple(
        db.scalars(
            select(Job.id).where(Job.status.in_(active_statuses)).order_by(Job.id).with_for_update()
        ).all()
    )
    if not job_ids:
        return ActiveJobCancellation(job_ids=(), cancelled_count=0)

    now = datetime.now(UTC)
    cleanup_excel_processing_rows(db, job_ids)
    result = db.execute(
        update(Job)
        .where(Job.id.in_(job_ids), Job.status.in_(active_statuses))
        .values(
            status=JOB_CANCELLED,
            error_code="CANCELLED_BY_ADMIN",
            error_message="Cancelled by administrator via bulk cancel.",
            progress_data={
                "type": "done",
                "status": JOB_CANCELLED,
                "message": "Cancelled by administrator via bulk cancel.",
                "error_code": "CANCELLED_BY_ADMIN",
            },
            finished_at=now,
            updated_at=now,
        )
    )
    from app.modules.dxf_classification.interface import (
        reconcile_dxf_classification_run_for_terminal_job,
    )

    for job_id in job_ids:
        reconcile_dxf_classification_run_for_terminal_job(
            db, job_id=job_id, attempt=db.get(Job, job_id).attempt
        )
    return ActiveJobCancellation(
        job_ids=job_ids,
        cancelled_count=result.rowcount or 0,
    )


def cancel_job(db: Session, job: Job) -> Job:
    """Atomically cancel one active job without overwriting a worker terminal state."""
    cancellable_statuses = (
        JOB_PENDING,
        JOB_QUEUED,
        JOB_RUNNING,
        JOB_VALIDATING,
        JOB_WAITING_CAD_WORKER,
    )
    if job.status not in cancellable_statuses:
        raise AppHTTPException(
            409,
            "JOB_NOT_CANCELLABLE",
            f"Job cannot be cancelled because it is already {job.status}.",
        )
    finished_at = datetime.now(UTC)
    payload = make_event(
        type_="done",
        status=JOB_CANCELLED,
        progress=job.progress,
        message="任务已取消",
        attempt=job.attempt,
    )
    payload["job_id"] = job.id
    result = execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job.id,
            Job.status.in_(cancellable_statuses),
            Job.attempt == job.attempt,
        )
        .values(
            status=JOB_CANCELLED,
            progress_data=payload,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        .execution_options(synchronize_session=False),
    )
    if result.rowcount != 1:
        db.rollback()
        current = db.get(Job, job.id, populate_existing=True)
        current_status = current.status if current is not None else "missing"
        raise AppHTTPException(
            409,
            "JOB_NOT_CANCELLABLE",
            f"Job cannot be cancelled because it is already {current_status}.",
        )
    cleanup_excel_processing_rows(db, (job.id,))
    from app.modules.dxf_classification.interface import (
        reconcile_dxf_classification_run_for_terminal_job,
    )

    reconcile_dxf_classification_run_for_terminal_job(
        db, job_id=job.id, attempt=job.attempt
    )
    db.expire(job)
    return db.get(Job, job.id, populate_existing=True) or job


def retry_job(db: Session, job: Job) -> Job:
    """Atomically enqueue a new generation for a failed or cancelled job."""
    if job.status not in (JOB_FAILED, JOB_CANCELLED):
        raise AppHTTPException(
            409,
            "JOB_NOT_RETRYABLE",
            f"Job cannot be retried because it is {job.status}. Only failed or cancelled jobs can be retried.",
        )
    previous_attempt = job.attempt
    next_attempt = previous_attempt + 1
    now = datetime.now(UTC)
    payload = make_event(
        type_="status",
        status=JOB_QUEUED,
        progress=0,
        message="任务已重新入队",
        attempt=next_attempt,
    )
    payload["job_id"] = job.id
    result = execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job.id,
            Job.status.in_((JOB_FAILED, JOB_CANCELLED)),
            Job.attempt == previous_attempt,
        )
        .values(
            status=JOB_QUEUED,
            attempt=next_attempt,
            progress=0,
            error_code=None,
            error_message=None,
            progress_data=payload,
            started_at=None,
            finished_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    if result.rowcount != 1:
        db.rollback()
        current = db.get(Job, job.id, populate_existing=True)
        current_status = current.status if current is not None else "missing"
        raise AppHTTPException(
            409,
            "JOB_NOT_RETRYABLE",
            f"Job cannot be retried because it is {current_status}.",
        )
    db.expire(job)
    return db.get(Job, job.id, populate_existing=True) or job


def rerun_succeeded_job(db: Session, job: Job) -> Job:
    """Enqueue a new immutable attempt after a business-level review outcome.

    Callers must first prove that the succeeded Job's domain run is waiting for
    review. This primitive deliberately does not make all succeeded Jobs retryable.
    """
    if job.status != JOB_SUCCEEDED:
        raise AppHTTPException(
            409,
            "JOB_NOT_RERUNNABLE",
            f"当前 Job 状态为 {job.status}，不能创建新的完整尝试。",
        )
    previous_attempt = job.attempt
    next_attempt = previous_attempt + 1
    now = datetime.now(UTC)
    payload = make_event(
        type_="status",
        status=JOB_QUEUED,
        progress=0,
        message="人工复核批次已创建新的完整尝试",
        attempt=next_attempt,
    )
    payload["job_id"] = job.id
    result = execute_guarded_job_update(
        db,
        update(Job)
        .where(
            Job.id == job.id,
            Job.status == JOB_SUCCEEDED,
            Job.attempt == previous_attempt,
        )
        .values(
            status=JOB_QUEUED,
            attempt=next_attempt,
            progress=0,
            error_code=None,
            error_message=None,
            progress_data=payload,
            started_at=None,
            finished_at=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False),
    )
    if result.rowcount != 1:
        db.rollback()
        current = db.get(Job, job.id, populate_existing=True)
        current_status = current.status if current is not None else "missing"
        raise AppHTTPException(
            409,
            "JOB_NOT_RERUNNABLE",
            f"当前 Job 状态为 {current_status}，不能创建新的完整尝试。",
        )
    db.expire(job)
    return db.get(Job, job.id, populate_existing=True) or job
