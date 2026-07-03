from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    get_db,
    has_global_project_access,
    require_project_member,
    require_project_role,
)
from app.core.exceptions import not_found
from app.models.drawing import Drawing
from app.models.job import Job, JobStep
from app.models.project import ProjectMember
from app.models.result import AnalysisResult
from app.schemas.common import ok, page_from_list
from app.schemas.job_schema import JobCreate, JobRead, JobStepRead
from app.schemas.result_schema import AnalysisResultRead
from app.services.audit_service import write_audit_log
from app.services.job_service import create_job, run_local_stub_job

router = APIRouter()
PROJECT_JOB_WRITE_ROLES = {"project_owner", "project_engineer"}


@router.get("")
def list_jobs(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = select(Job).order_by(Job.id.desc())
    if not has_global_project_access(current_user):
        stmt = stmt.join(ProjectMember, ProjectMember.project_id == Job.project_id).where(
            ProjectMember.user_id == current_user.id
        )
    jobs = list(db.scalars(stmt).all())
    return page_from_list(
        [JobRead.model_validate(j) for j in jobs], page, page_size, request.state.request_id
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_job_api(
    payload: JobCreate,
    background_tasks: BackgroundTasks,
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
    job = create_job(db, payload, created_by=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="jobs.create",
        resource_type="job",
        resource_id=job.id,
        after_json=payload.model_dump(),
    )
    db.commit()
    background_tasks.add_task(run_local_stub_job, job.id)
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.get("/{job_id}")
def get_job(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    if job.project_id is not None:
        require_project_member(db, current_user, job.project_id)
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.post("/{job_id}/cancellation-requests", status_code=status.HTTP_202_ACCEPTED)
def cancel_job(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    if job.project_id is not None:
        require_project_role(db, current_user, job.project_id, PROJECT_JOB_WRITE_ROLES)
    job.status = "cancelled"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="jobs.cancel",
        resource_type="job",
        resource_id=job.id,
    )
    db.commit()
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.post("/{job_id}/retry-requests", status_code=status.HTTP_202_ACCEPTED)
def retry_job(
    job_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    if job.project_id is not None:
        require_project_role(db, current_user, job.project_id, PROJECT_JOB_WRITE_ROLES)
    job.status = "queued"
    job.progress = 0
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="jobs.retry",
        resource_type="job",
        resource_id=job.id,
    )
    db.commit()
    background_tasks.add_task(run_local_stub_job, job.id)
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.get("/{job_id}/steps")
def get_job_steps(
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
    if job.project_id is not None:
        require_project_member(db, current_user, job.project_id)
    steps = list(
        db.scalars(select(JobStep).where(JobStep.job_id == job_id).order_by(JobStep.id)).all()
    )
    return page_from_list(
        [JobStepRead.model_validate(s) for s in steps], page, page_size, request.state.request_id
    )


@router.get("/{job_id}/logs")
def get_job_logs(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    if job.project_id is not None:
        require_project_member(db, current_user, job.project_id)
    return ok(
        {
            "job_id": job_id,
            "logs": [],
            "message": "Structured worker logs are not wired in stage 1.",
        },
        request.state.request_id,
    )


@router.get("/{job_id}/events")
def get_job_events(
    job_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    if job.project_id is not None:
        require_project_member(db, current_user, job.project_id)
    return ok(
        {
            "job_id": job_id,
            "events": [],
            "message": "SSE will be implemented after async queue is introduced.",
        },
        request.state.request_id,
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
    if job.project_id is not None:
        require_project_member(db, current_user, job.project_id)
    results = list(
        db.scalars(
            select(AnalysisResult)
            .where(AnalysisResult.job_id == job_id)
            .order_by(AnalysisResult.id)
        ).all()
    )
    return page_from_list(
        [AnalysisResultRead.model_validate(r) for r in results],
        page,
        page_size,
        request.state.request_id,
    )
