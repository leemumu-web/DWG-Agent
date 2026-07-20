"""Job creation, cancellation and retry HTTP commands."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, require_file_read_access
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.access import PROJECT_JOB_WRITE_ROLES, require_job_write_access
from app.modules.jobs.creation import create_conversion_jobs, create_job
from app.modules.jobs.dispatch import (
    dispatch_committed_conversion_batch,
    dispatch_committed_job,
)
from app.modules.jobs.lifecycle import (
    cancel_active_jobs_in_transaction,
)
from app.modules.jobs.lifecycle import (
    cancel_job as transition_job_to_cancelled,
)
from app.modules.jobs.lifecycle import (
    retry_job as transition_job_to_queued,
)
from app.modules.jobs.models import Job
from app.modules.jobs.schemas import ConversionBatchCreate, JobBulkCancellation, JobCreate, JobRead
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import Drawing, has_global_project_access, require_project_role
from app.platform.config.constants import (
    TASK_DWG_TO_DXF,
    TASK_DXF_TO_DWG,
    TASK_DXF_TO_EXCEL,
    TASK_EXCEL_FINAL,
)
from app.platform.config.settings import settings
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException, not_found, service_unavailable

static_router = APIRouter()
item_router = APIRouter()
logger = logging.getLogger(__name__)


@static_router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_job_api(
    payload: JobCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if payload.project_id is not None:
        require_project_role(db, current_user, payload.project_id, PROJECT_JOB_WRITE_ROLES)
    elif payload.drawing_id is not None:
        drawing = db.get(Drawing, payload.drawing_id)
        if not drawing or drawing.status == "deleted":
            raise not_found("Drawing")
        require_project_role(db, current_user, drawing.project_id, PROJECT_JOB_WRITE_ROLES)
    # Pipeline feature gates
    if payload.task_type == TASK_DWG_TO_DXF and not settings.dxf_pipeline_enabled:
        raise service_unavailable(
            "DXF_PIPELINE_DISABLED",
            "DWG→DXF pipeline is disabled. Set DXF_PIPELINE_ENABLED=true to enable.",
        )
    if payload.task_type == TASK_DXF_TO_DWG and not settings.dxf2dwg_pipeline_enabled:
        raise service_unavailable(
            "DXF2DWG_PIPELINE_DISABLED",
            "DXF→DWG pipeline is disabled. Set DXF2DWG_PIPELINE_ENABLED=true to enable.",
        )
    if payload.task_type == TASK_DXF_TO_EXCEL and not settings.dxf2excel_pipeline_enabled:
        raise service_unavailable(
            "DXF2EXCEL_PIPELINE_DISABLED",
            "DXF→Excel pipeline is disabled. Set DXF2EXCEL_PIPELINE_ENABLED=true to enable.",
        )
    if payload.task_type == TASK_EXCEL_FINAL and not settings.excel_final_pipeline_enabled:
        raise service_unavailable(
            "EXCEL_FINAL_PIPELINE_DISABLED",
            "Excel→Final pipeline is disabled. Set EXCEL_FINAL_PIPELINE_ENABLED=true to enable.",
        )
    job = create_job(db, payload, created_by=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="jobs.create",
        resource_type="job",
        resource_id=job.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    dispatch_committed_job(db, job)
    return ok(JobRead.model_validate(job), request.state.request_id)


@static_router.post("/batches", status_code=status.HTTP_202_ACCEPTED)
def create_conversion_batch(
    payload: ConversionBatchCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if payload.task_type == TASK_DWG_TO_DXF and not settings.dxf_pipeline_enabled:
        raise service_unavailable(
            "DXF_PIPELINE_DISABLED",
            "DWG→DXF pipeline is disabled. Set DXF_PIPELINE_ENABLED=true to enable.",
        )
    if payload.task_type == TASK_DXF_TO_DWG and not settings.dxf2dwg_pipeline_enabled:
        raise service_unavailable(
            "DXF2DWG_PIPELINE_DISABLED",
            "DXF→DWG pipeline is disabled. Set DXF2DWG_PIPELINE_ENABLED=true to enable.",
        )

    unique_ids = list(dict.fromkeys(payload.file_ids))
    for file_id in unique_ids:
        stored = db.get(StoredFile, file_id)
        if stored is None or stored.status == "deleted":
            raise not_found("File")
        require_file_read_access(db, current_user, stored)

    jobs = create_conversion_jobs(
        db,
        task_type=payload.task_type,
        file_ids=unique_ids,
        precision_level=payload.precision_level,
        created_by=current_user.id,
    )
    for job in jobs:
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="jobs.create",
            resource_type="job",
            resource_id=job.id,
            after_json={
                "task_type": payload.task_type,
                "precision_level": payload.precision_level,
                "params": job.params_json,
                "batch": True,
            },
            request=request,
        )
    db.commit()
    dispatch_committed_conversion_batch(
        task_type=payload.task_type,
        jobs=[(job.id, job.attempt) for job in jobs],
    )
    return ok(
        {"jobs": [JobRead.model_validate(job) for job in jobs]},
        request.state.request_id,
    )


@static_router.post("/cancellation-requests", status_code=status.HTTP_202_ACCEPTED)
def cancel_jobs(
    payload: JobBulkCancellation,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    job_ids = list(dict.fromkeys(payload.job_ids))
    jobs: list[Job] = []
    for job_id in job_ids:
        job = db.get(Job, job_id)
        if job is None:
            raise not_found("Job")
        require_job_write_access(db, current_user, job)
        jobs.append(job)

    cancelled = [transition_job_to_cancelled(db, job) for job in jobs]
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="jobs.cancel_batch",
        resource_type="job",
        resource_id=0,
        after_json={"cancelled_job_ids": [job.id for job in cancelled]},
        request=request,
    )
    db.commit()
    return ok(
        {
            "cancelled_count": len(cancelled),
            "cancelled_job_ids": [job.id for job in cancelled],
        },
        request.state.request_id,
    )


@static_router.post("/cancel-all-active")
def cancel_all_active(
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Cancel all currently queued or running jobs (admin only).
    Best-effort Celery revocation accompanies the DB update.

    Uses a bulk SQL UPDATE to avoid the MySQL 1020 error caused by
    the Celery worker modifying the same rows concurrently."""
    from app.platform.messaging.celery_app import purge_queued_job_messages

    if not has_global_project_access(current_user):
        raise AppHTTPException(403, "FORBIDDEN", "Only administrators can cancel all jobs.")

    cancellation = cancel_active_jobs_in_transaction(db)
    cancelled_ids = list(cancellation.job_ids)
    cancelled_count = cancellation.cancelled_count

    # Single audit log for the batch operation
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="jobs.cancel_all",
        resource_type="job",
        resource_id=0,
        after_json={"cancelled_count": cancelled_count, "cancelled_ids": cancelled_ids},
        request=request,
    )
    db.commit()

    # SQLAlchemy transport has no fanout/remote-control support, so workers
    # converge on the cancelled DB state and queued messages are purged directly.
    purged_by_queue: dict[str, int] = {}
    purge_errors: dict[str, str] = {}
    try:
        purged_by_queue, purge_errors = purge_queued_job_messages()
        if purge_errors:
            logger.warning("Failed to purge Celery queues: %s", purge_errors)
    except Exception as exc:
        purge_errors = {"__connection__": str(exc) or exc.__class__.__name__}
        logger.exception("Celery queue purge failed")

    purged_count = sum(purged_by_queue.values())

    return ok(
        {
            "cancelled_count": cancelled_count,
            "celery_revoked": purged_count,
            "broker_purged_by_queue": purged_by_queue,
            "broker_purge_failed_queues": sorted(purge_errors),
        },
        request.state.request_id,
    )


@item_router.post("/{job_id}/cancellation-requests", status_code=status.HTTP_202_ACCEPTED)
def cancel_job(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_write_access(db, current_user, job)
    job = transition_job_to_cancelled(db, job)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="jobs.cancel",
        resource_type="job",
        resource_id=job.id,
        request=request,
    )
    db.commit()
    return ok(JobRead.model_validate(job), request.state.request_id)


@item_router.post("/{job_id}/retry-requests", status_code=status.HTTP_202_ACCEPTED)
def retry_job(
    job_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_write_access(db, current_user, job)
    job = transition_job_to_queued(db, job)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="jobs.retry",
        resource_type="job",
        resource_id=job.id,
        request=request,
    )
    db.commit()
    dispatch_committed_job(db, job)
    return ok(JobRead.model_validate(job), request.state.request_id)
