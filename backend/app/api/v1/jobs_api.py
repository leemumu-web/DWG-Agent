from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import String, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import (
    CurrentUser,
    CurrentUserForSSE,
    get_db,
    has_global_project_access,
    require_project_role,
)
from app.models.drawing import Drawing
from app.models.excel_final import ExcelFinalBatch
from app.models.file import StoredFile
from app.models.job import Job, JobStep
from app.models.result import AnalysisResult
from app.platform.config.constants import (
    TASK_DWG_TO_DXF,
    TASK_DXF_TO_DWG,
    TASK_DXF_TO_EXCEL,
    TASK_EXCEL_FINAL,
)
from app.platform.config.settings import settings
from app.platform.config.validators import validate_sort_by
from app.platform.database.pagination import paginate_scalars
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found, service_unavailable
from app.schemas.job_schema import (
    ConversionBatchCreate,
    JobBulkCancellation,
    JobCreate,
    JobRead,
    JobStepRead,
)
from app.schemas.result_schema import AnalysisResultRead
from app.services.audit_service import write_audit_log
from app.services.file_service import require_file_read_access
from app.services.job_access import (
    PROJECT_JOB_WRITE_ROLES,
    job_read_filter,
    require_job_read_access,
    require_job_write_access,
)
from app.services.job_events import job_event_stream, jobs_event_stream
from app.services.job_service import (
    cancel_job as transition_job_to_cancelled,
)
from app.services.job_service import (
    create_conversion_jobs,
    create_job,
    dispatch_committed_conversion_batch,
    dispatch_committed_job,
)
from app.services.job_service import (
    retry_job as transition_job_to_queued,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("")
def list_jobs(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    task_type: str = Query("", description="Filter by task type, e.g. 'convert_dwg_to_dxf'"),
    status_filter: str = Query("", alias="status", max_length=32),
    search: str = Query("", max_length=100),
    file_ids: str = Query("", max_length=2200),
    latest_per_file: bool = Query(False),
    db: Session = Depends(get_db),
):
    sort_column = validate_sort_by("jobs", sort_by)
    sort_dir_value = sort_dir.strip().lower()
    order_clause = getattr(Job, sort_column)
    if sort_dir_value == "asc":
        order_clause = order_clause.asc()
    else:
        order_clause = order_clause.desc()
    tie_breaker = Job.id.asc() if sort_dir_value == "asc" else Job.id.desc()
    stmt = select(Job).order_by(order_clause, tie_breaker)
    if task_type.strip():
        stmt = stmt.where(Job.task_type == task_type.strip())
    if status_filter.strip() == "active":
        stmt = stmt.where(
            Job.status.in_(
                {"pending", "queued", "running", "validating", "waiting_cad_worker"}
            )
        )
    elif status_filter.strip():
        stmt = stmt.where(Job.status == status_filter.strip())
    if search.strip():
        pattern = f"%{search.strip()}%"
        search_clauses = [
            Job.task_type.ilike(pattern),
            Job.pipeline.ilike(pattern),
            cast(Job.id, String).like(pattern),
        ]
        stmt = stmt.where(or_(*search_clauses))
    parsed_file_ids: set[int] | None = None
    if file_ids.strip():
        try:
            parsed_file_ids = {int(value) for value in file_ids.split(",") if value}
        except ValueError as exc:
            raise AppHTTPException(
                422, "INVALID_PARAMS", "file_ids must be comma-separated integers."
            ) from exc
        if not parsed_file_ids or len(parsed_file_ids) > 200:
            raise AppHTTPException(
                422, "INVALID_PARAMS", "file_ids must contain between 1 and 200 ids."
            )
        stmt = stmt.where(Job.params_json["file_id"].as_integer().in_(parsed_file_ids))
    if latest_per_file:
        if not parsed_file_ids:
            raise AppHTTPException(
                422, "INVALID_PARAMS", "latest_per_file requires file_ids."
            )
        file_id_expression = Job.params_json["file_id"].as_integer()
        latest_ids = select(func.max(Job.id)).where(file_id_expression.in_(parsed_file_ids))
        if task_type.strip():
            latest_ids = latest_ids.where(Job.task_type == task_type.strip())
        if not has_global_project_access(current_user):
            latest_ids = latest_ids.where(job_read_filter(current_user))
        latest_ids = latest_ids.group_by(file_id_expression)
        stmt = stmt.where(Job.id.in_(latest_ids))
    if not has_global_project_access(current_user):
        stmt = stmt.where(job_read_filter(current_user))
    jobs, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    return page_response(
        [JobRead.model_validate(j) for j in jobs],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
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


@router.post("/batches", status_code=status.HTTP_202_ACCEPTED)
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


@router.post("/cancellation-requests", status_code=status.HTTP_202_ACCEPTED)
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


@router.get("/events/stream")
def get_conversion_events(
    task_type: str,
    file_ids: str,
    current_user: CurrentUserForSSE,
    db: Session = Depends(get_db),
):
    """Stream the latest jobs for an ordered set of conversion source files."""
    if task_type not in {TASK_DWG_TO_DXF, TASK_DXF_TO_DWG}:
        raise AppHTTPException(
            422,
            "INVALID_PARAMS",
            "task_type must be a supported bidirectional CAD conversion.",
        )
    try:
        requested_file_ids = tuple(
            dict.fromkeys(int(value) for value in file_ids.split(",") if value.strip())
        )
    except ValueError as exc:
        raise AppHTTPException(
            422, "INVALID_PARAMS", "file_ids must be comma-separated integers."
        ) from exc
    if not requested_file_ids or len(requested_file_ids) > 200:
        raise AppHTTPException(
            422, "INVALID_PARAMS", "file_ids must contain between 1 and 200 ids."
        )

    candidates = list(
        db.scalars(
            select(Job)
            .where(
                Job.task_type == task_type,
                Job.params_json["file_id"].as_integer().in_(requested_file_ids),
            )
            .order_by(Job.id.desc())
        ).all()
    )
    latest_by_file: dict[int, Job] = {}
    for job in candidates:
        raw_file_id = (job.params_json or {}).get("file_id")
        if isinstance(raw_file_id, int):
            latest_by_file.setdefault(raw_file_id, job)
    jobs = [latest_by_file[file_id] for file_id in requested_file_ids if file_id in latest_by_file]
    if not jobs:
        raise not_found("Job")
    for job in jobs:
        require_job_read_access(db, current_user, job)

    bind = db.get_bind()
    stream_sessions = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    job_ids = [job.id for job in jobs]
    db.rollback()

    def event_stream():
        first = True
        for snapshot in jobs_event_stream(stream_sessions, job_ids):
            if snapshot is None:
                yield ": keepalive\n\n"
                continue
            payload = {
                "type": "snapshot" if first else "update",
                "jobs": snapshot,
            }
            first = False
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{job_id}")
def get_job(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_read_access(db, current_user, job)
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.post("/{job_id}/cancellation-requests", status_code=status.HTTP_202_ACCEPTED)
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


@router.post("/{job_id}/retry-requests", status_code=status.HTTP_202_ACCEPTED)
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


@router.get("/{job_id}/steps")
def get_job_steps(
    job_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    attempt: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_read_access(db, current_user, job)
    stmt = select(JobStep).where(JobStep.job_id == job_id)
    if attempt is not None:
        stmt = stmt.where(JobStep.attempt == attempt)
    steps, total = paginate_scalars(
        db,
        stmt.order_by(JobStep.attempt, JobStep.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [JobStepRead.model_validate(s) for s in steps],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.get("/{job_id}/logs")
def get_job_logs(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_read_access(db, current_user, job)
    return ok(
        {
            "job_id": job_id,
            "logs": [],
            "message": "Structured worker logs are not wired in stage 1.",
        },
        request.state.request_id,
    )


def _job_snapshot(db: Session, job_id: int) -> dict:
    """从 DB 读取当前任务状态快照，用于 SSE 初始帧和终态兜底。"""
    job = db.get(Job, job_id)
    if not job:
        return {"type": "snapshot", "job_id": job_id, "status": "unknown"}
    steps = list(
        db.scalars(
            select(JobStep)
            .where(JobStep.job_id == job_id, JobStep.attempt == job.attempt)
            .order_by(JobStep.id)
        ).all()
    )
    return {
        "type": "snapshot",
        "job_id": job_id,
        "status": job.status,
        "attempt": job.attempt,
        "progress": job.progress,
        "pipeline": job.pipeline,
        "task_type": job.task_type,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "progress_data": job.progress_data,
        "steps": [
            {
                "attempt": s.attempt,
                "step_name": s.step_name,
                "status": s.status,
                "error_message": s.error_message,
            }
            for s in steps
        ],
    }


@router.get("/{job_id}/events")
def get_job_events(
    job_id: int, request: Request, current_user: CurrentUserForSSE, db: Session = Depends(get_db)
):
    """Server-Sent Events 端点：从 MySQL 任务行流式推送进度。

    先发送数据库快照，再以短事务轮询最新状态；空轮次发送 keepalive。
    终态事件后再发送一帧终态快照兜底。

    鉴权：Authorization header 优先；浏览器 EventSource 使用仅限 jobs 路径的
    HttpOnly 短期 Cookie，避免把访问令牌写入 URL 和访问日志。
    """
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_read_access(db, current_user, job)

    terminal = {"succeeded", "failed", "cancelled"}
    initial_snapshot = _job_snapshot(db, job_id)
    bind = db.get_bind()
    stream_sessions = sessionmaker(
        bind=bind,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    # Release the request transaction before the long-lived response starts.
    db.rollback()

    def event_stream():
        yield f"data: {json.dumps(initial_snapshot, ensure_ascii=False)}\n\n"
        if initial_snapshot["status"] in terminal:
            return

        stream = job_event_stream(stream_sessions, job_id)
        for event in stream:
            if event is None:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in {"done", "error"}:
                break

        with stream_sessions() as final_db:
            final_snapshot = _job_snapshot(final_db, job_id)
        yield f"data: {json.dumps(final_snapshot, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{job_id}/results")
def get_job_results(
    job_id: int,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_read_access(db, current_user, job)
    results, total = paginate_scalars(
        db,
        select(AnalysisResult)
        .where(AnalysisResult.job_id == job_id)
        .order_by(AnalysisResult.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [AnalysisResultRead.model_validate(r) for r in results],
        page,
        page_size,
        total,
        request.state.request_id,
    )


# ── global control ───────────────────────────────────────────────────────────


@router.post("/cancel-all-active")
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

    # Lock the exact candidate set so the audit record cannot pick up rows from
    # an earlier bulk cancellation that happened to share timestamp precision.
    active_statuses = ("queued", "running", "pending")
    cancelled_ids = list(
        db.scalars(
            select(Job.id)
            .where(Job.status.in_(active_statuses))
            .order_by(Job.id)
            .with_for_update()
        ).all()
    )
    now = datetime.now(UTC)
    if cancelled_ids:
        db.execute(
            delete(ExcelFinalBatch).where(ExcelFinalBatch.job_id.in_(cancelled_ids))
        )
        result = db.execute(
            update(Job)
            .where(Job.id.in_(cancelled_ids), Job.status.in_(active_statuses))
            .values(
                status="cancelled",
                error_code="CANCELLED_BY_ADMIN",
                error_message="Cancelled by administrator via bulk cancel.",
                progress_data={
                    "type": "done",
                    "status": "cancelled",
                    "message": "Cancelled by administrator via bulk cancel.",
                    "error_code": "CANCELLED_BY_ADMIN",
                },
                finished_at=now,
                updated_at=now,
            )
        )
        cancelled_count = result.rowcount or 0
    else:
        cancelled_count = 0

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
