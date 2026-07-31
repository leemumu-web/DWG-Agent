"""Transactional staging for durable Job delivery intents."""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.jobs.models import Job, JobDispatch
from app.platform.config.constants import JOB_FAILED, JOB_QUEUED, PIPELINE_STUB
from app.platform.http.exceptions import AppHTTPException
from app.platform.time import business_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatchLease:
    dispatch_uid: str
    lease_token: str
    mode: str
    task_type: str
    pipeline: str
    jobs: tuple[tuple[int, int], ...]


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


def retry_delay(attempt: int) -> float:
    """Return bounded exponential delay with equal jitter."""
    ceiling = min(30.0, 0.5 * (2 ** min(max(attempt, 0), 6)))
    return ceiling / 2 + random.uniform(0.0, ceiling / 2)


def lease_next_dispatch(
    factory: sessionmaker[Session],
    *,
    lease_seconds: int = 30,
) -> DispatchLease | None:
    """Lease one whole dispatch group in a short committed transaction."""
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    now = business_now()
    with factory() as db:
        db.execute(
            update(JobDispatch)
            .where(
                JobDispatch.status == "leased",
                JobDispatch.lease_expires_at <= now,
            )
            .values(
                status="pending",
                lease_token=None,
                lease_expires_at=None,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        leader_ids = select(func.min(JobDispatch.id)).group_by(JobDispatch.dispatch_uid)
        leader = db.scalar(
            select(JobDispatch)
            .where(
                JobDispatch.id.in_(leader_ids),
                JobDispatch.status == "pending",
                JobDispatch.available_at <= now,
            )
            .order_by(JobDispatch.available_at, JobDispatch.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if leader is None:
            db.commit()
            return None
        rows = list(
            db.scalars(
                select(JobDispatch)
                .where(JobDispatch.dispatch_uid == leader.dispatch_uid)
                .order_by(JobDispatch.job_id, JobDispatch.job_attempt)
                .with_for_update()
            )
        )
        modes = {row.dispatch_mode for row in rows}
        task_types = {row.task_type for row in rows}
        pipelines = {row.pipeline for row in rows}
        if (
            not rows
            or any(row.status != "pending" or row.available_at > now for row in rows)
            or len(modes) != 1
            or len(task_types) != 1
            or len(pipelines) != 1
        ):
            mode = "invalid"
            task_type = "invalid"
            pipeline = "invalid"
        else:
            mode = modes.pop()
            task_type = task_types.pop()
            pipeline = pipelines.pop()
        lease_token = str(uuid4())
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        for row in rows:
            row.status = "leased"
            row.lease_token = lease_token
            row.lease_expires_at = lease_expires_at
        jobs = tuple((row.job_id, row.job_attempt) for row in rows)
        dispatch_uid = leader.dispatch_uid
        db.commit()
        return DispatchLease(
            dispatch_uid=dispatch_uid,
            lease_token=lease_token,
            mode=mode,
            task_type=task_type,
            pipeline=pipeline,
            jobs=jobs,
        )


def _locked_lease_rows(db: Session, lease: DispatchLease) -> list[JobDispatch]:
    rows = list(
        db.scalars(
            select(JobDispatch)
            .where(JobDispatch.dispatch_uid == lease.dispatch_uid)
            .order_by(JobDispatch.job_id, JobDispatch.job_attempt)
            .with_for_update()
        )
    )
    actual_jobs = tuple((row.job_id, row.job_attempt) for row in rows)
    if (
        actual_jobs != lease.jobs
        or any(
            row.status != "leased" or row.lease_token != lease.lease_token
            for row in rows
        )
    ):
        return []
    return rows


def mark_dispatch_delivered(
    factory: sessionmaker[Session],
    lease: DispatchLease,
    *,
    celery_task_id: str,
) -> bool:
    now = business_now()
    with factory() as db:
        rows = _locked_lease_rows(db, lease)
        if not rows:
            db.rollback()
            return False
        for row in rows:
            row.status = "delivered"
            row.celery_task_id = celery_task_id[:64]
            row.delivered_at = now
            row.lease_token = None
            row.lease_expires_at = None
            row.last_error_code = None
            row.last_error_message = None
        db.commit()
    return True


def _settle_publish_failure(
    factory: sessionmaker[Session],
    lease: DispatchLease,
    *,
    permanent: bool,
) -> bool:
    now = business_now()
    error_code = (
        "JOB_DISPATCH_UNSUPPORTED" if permanent else "JOB_DISPATCH_TEMPORARY_FAILURE"
    )
    error_message = (
        "The staged Job dispatch is not supported by this release."
        if permanent
        else "Broker delivery failed temporarily and will be retried."
    )
    with factory() as db:
        rows = _locked_lease_rows(db, lease)
        if not rows:
            db.rollback()
            return False
        delivery_attempt = max(row.delivery_attempts for row in rows) + 1
        for row in rows:
            row.delivery_attempts = delivery_attempt
            row.status = "failed" if permanent else "pending"
            row.available_at = (
                now
                if permanent
                else now + timedelta(seconds=retry_delay(delivery_attempt))
            )
            row.lease_token = None
            row.lease_expires_at = None
            row.last_error_code = error_code
            row.last_error_message = error_message
        if permanent:
            for job_id, attempt in lease.jobs:
                event = {
                    "job_id": job_id,
                    "attempt": attempt,
                    "type": "error",
                    "status": JOB_FAILED,
                    "progress": 0,
                    "error_code": error_code,
                    "error_message": error_message,
                    "message": error_message,
                }
                db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.attempt == attempt,
                        Job.status == JOB_QUEUED,
                    )
                    .values(
                        status=JOB_FAILED,
                        progress=0,
                        progress_data=event,
                        error_code=error_code,
                        error_message=error_message,
                        finished_at=now,
                        updated_at=now,
                    )
                    .execution_options(synchronize_session=False)
                )
        db.commit()
    return True


def publish_dispatch(lease: DispatchLease) -> str:
    """Publish after the leasing transaction has committed."""
    from app.modules.jobs.dispatch import publish_dispatch as publish

    return publish(lease)


def drain_once(factory: sessionmaker[Session]) -> bool:
    """Lease and settle at most one dispatch group without sleeping."""
    lease = lease_next_dispatch(factory)
    if lease is None:
        return False
    try:
        celery_task_id = publish_dispatch(lease)
    except Exception as exc:
        from app.modules.jobs.dispatch import PermanentDispatchError

        permanent = isinstance(exc, PermanentDispatchError)
        _settle_publish_failure(factory, lease, permanent=permanent)
        logger.warning(
            "Job dispatch publish failed uid=%s category=%s",
            lease.dispatch_uid,
            "permanent" if permanent else "transient",
        )
        return True
    marked = mark_dispatch_delivered(
        factory,
        lease,
        celery_task_id=celery_task_id,
    )
    if not marked:
        logger.warning("Job dispatch lease was lost before settlement uid=%s", lease.dispatch_uid)
    return True


__all__ = [
    "DispatchLease",
    "drain_once",
    "lease_next_dispatch",
    "mark_dispatch_delivered",
    "publish_dispatch",
    "retry_delay",
    "stage_conversion_dispatch",
    "stage_job_dispatch",
]
