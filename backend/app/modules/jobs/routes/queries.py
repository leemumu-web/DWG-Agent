"""Job collection and item queries."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.access import job_read_filter, require_job_read_access
from app.modules.jobs.diagnostics import build_job_diagnostics
from app.modules.jobs.models import AnalysisResult, Job, JobStep
from app.modules.jobs.schemas import JobRead, JobStepRead
from app.modules.projects.interface import has_global_project_access
from app.platform.config.validators import validate_sort_by
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found

static_router = APIRouter()
item_router = APIRouter()


@static_router.get("")
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
            Job.status.in_({"pending", "queued", "running", "validating", "waiting_cad_worker"})
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
            raise AppHTTPException(422, "INVALID_PARAMS", "latest_per_file requires file_ids.")
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
    available_result_pairs: set[tuple[int, str]] = set()
    if latest_per_file:
        succeeded_job_ids = [job.id for job in jobs if job.status == "succeeded"]
        if succeeded_job_ids:
            available_result_pairs = set(
                db.execute(
                    select(AnalysisResult.job_id, AnalysisResult.result_type)
                    .join(StoredFile, StoredFile.id == AnalysisResult.result_file_id)
                    .where(
                        AnalysisResult.job_id.in_(succeeded_job_ids),
                        AnalysisResult.status == "succeeded",
                        StoredFile.status == "available",
                        StoredFile.deleted_at.is_(None),
                        StoredFile.purged_at.is_(None),
                    )
                ).all()
            )
    payloads: list[JobRead] = []
    for job in jobs:
        payload = JobRead.model_validate(job)
        if latest_per_file and job.status == "succeeded":
            payload.result_available = (job.id, job.task_type) in available_result_pairs
        payloads.append(payload)
    return page_response(
        payloads,
        page,
        page_size,
        total,
        request.state.request_id,
    )


@item_router.get("/{job_id}")
def get_job(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_read_access(db, current_user, job)
    return ok(JobRead.model_validate(job), request.state.request_id)


@item_router.get("/{job_id}/steps")
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


@item_router.get("/{job_id}/logs")
def get_job_logs(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    require_job_read_access(db, current_user, job)
    return ok(build_job_diagnostics(db, job), request.state.request_id)
