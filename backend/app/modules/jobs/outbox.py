"""Transactional staging for durable Job delivery intents."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.jobs.models import Job, JobDispatch
from app.platform.config.constants import JOB_QUEUED, PIPELINE_STUB
from app.platform.http.exceptions import AppHTTPException
from app.platform.time import business_now


def _attempt_key(job: Job) -> tuple[int, int]:
    if job.id is None:
        raise AppHTTPException(
            409,
            "JOB_DISPATCH_JOB_UNPERSISTED",
            "A Job must be persisted before its dispatch intent is staged.",
        )
    return job.id, job.attempt


def _validate_jobs(*, task_type: str, jobs: list[Job]) -> list[tuple[int, int]]:
    attempts: list[tuple[int, int]] = []
    for job in jobs:
        if job.status != JOB_QUEUED:
            raise AppHTTPException(
                409,
                "JOB_DISPATCH_STATE_INVALID",
                "Only a queued Job attempt can be staged for delivery.",
                {"job_id": job.id, "attempt": job.attempt, "status": job.status},
            )
        if job.task_type != task_type:
            raise AppHTTPException(
                409,
                "JOB_DISPATCH_TASK_MISMATCH",
                "The dispatch task type does not match its Job.",
                {"job_id": job.id, "job_task_type": job.task_type},
            )
        attempts.append(_attempt_key(job))
    if len(set(attempts)) != len(attempts):
        raise AppHTTPException(
            409,
            "JOB_DISPATCH_SET_CONFLICT",
            "The dispatch set contains the same Job attempt more than once.",
        )
    return attempts


def _load_existing(
    db: Session,
    attempts: list[tuple[int, int]],
    *,
    lock: bool = False,
) -> list[JobDispatch]:
    if not attempts:
        return []
    statement = select(JobDispatch).where(
        tuple_(JobDispatch.job_id, JobDispatch.job_attempt).in_(attempts)
    )
    if lock:
        statement = statement.with_for_update()
    return list(db.scalars(statement.order_by(JobDispatch.job_id, JobDispatch.job_attempt)))


def _require_complete_existing_set(
    rows: list[JobDispatch],
    attempts: list[tuple[int, int]],
    *,
    task_type: str,
    dispatch_mode: str,
) -> list[JobDispatch]:
    expected = set(attempts)
    actual = {(row.job_id, row.job_attempt) for row in rows}
    valid_group = (
        actual == expected
        and all(row.task_type == task_type for row in rows)
        and all(row.dispatch_mode == dispatch_mode for row in rows)
        and (dispatch_mode == "single" or len({row.dispatch_uid for row in rows}) == 1)
    )
    if not valid_group:
        raise AppHTTPException(
            409,
            "JOB_DISPATCH_SET_CONFLICT",
            "Only part of the Job batch already has a compatible dispatch intent.",
        )
    return rows


def _stage_dispatch(
    db: Session,
    *,
    task_type: str,
    jobs: list[Job],
    dispatch_mode: str,
) -> list[JobDispatch]:
    attempts = _validate_jobs(task_type=task_type, jobs=jobs)
    if not attempts:
        return []
    existing = _load_existing(db, attempts)
    if existing:
        return _require_complete_existing_set(
            existing,
            attempts,
            task_type=task_type,
            dispatch_mode=dispatch_mode,
        )

    dispatch_uid = str(uuid4())
    rows = [
        JobDispatch(
            dispatch_uid=dispatch_uid,
            job_id=job.id,
            job_attempt=job.attempt,
            task_type=task_type,
            pipeline=job.pipeline or PIPELINE_STUB,
            dispatch_mode=dispatch_mode,
            status="pending",
            delivery_attempts=0,
            available_at=business_now(),
        )
        for job in jobs
    ]
    try:
        with db.begin_nested():
            db.add_all(rows)
            db.flush()
    except IntegrityError:
        existing = _load_existing(db, attempts, lock=True)
        if not existing:
            raise
        return _require_complete_existing_set(
            existing,
            attempts,
            task_type=task_type,
            dispatch_mode=dispatch_mode,
        )
    return sorted(rows, key=lambda row: (row.job_id, row.job_attempt))


def stage_conversion_dispatch(
    db: Session,
    *,
    task_type: str,
    jobs: list[Job],
) -> list[JobDispatch]:
    """Stage one stable batch message for ordered conversion Job attempts."""
    return _stage_dispatch(
        db,
        task_type=task_type,
        jobs=jobs,
        dispatch_mode="conversion_batch",
    )


def stage_job_dispatch(db: Session, job: Job) -> JobDispatch:
    """Stage one stable message for the current queued Job attempt."""
    return _stage_dispatch(
        db,
        task_type=job.task_type,
        jobs=[job],
        dispatch_mode="single",
    )[0]


__all__ = ["stage_conversion_dispatch", "stage_job_dispatch"]
