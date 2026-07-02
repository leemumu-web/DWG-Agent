from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import CurrentUser, get_db
from backend.app.core.exceptions import not_found
from backend.app.models.job import Job, JobStep
from backend.app.models.result import AnalysisResult
from backend.app.schemas.common import ok, page
from backend.app.schemas.job_schema import JobCreate, JobRead, JobStepRead
from backend.app.schemas.result_schema import AnalysisResultRead
from backend.app.services.audit_service import write_audit_log
from backend.app.services.job_service import create_job, run_local_stub_job

router = APIRouter()


@router.get("")
def list_jobs(request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    jobs = list(db.scalars(select(Job).order_by(Job.id.desc())).all())
    return page([JobRead.model_validate(j) for j in jobs], 1, len(jobs), len(jobs), request.state.request_id)


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_job_api(payload: JobCreate, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    job = create_job(db, payload, created_by=current_user.id)
    write_audit_log(db, actor_user_id=current_user.id, action="jobs.create", resource_type="job", resource_id=job.id, after_json=payload.model_dump())
    db.commit()
    background_tasks.add_task(run_local_stub_job, job.id)
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.get("/{job_id}")
def get_job(job_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.post("/{job_id}/cancellation-requests", status_code=status.HTTP_202_ACCEPTED)
def cancel_job(job_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    job.status = "cancelled"
    write_audit_log(db, actor_user_id=current_user.id, action="jobs.cancel", resource_type="job", resource_id=job.id)
    db.commit()
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.post("/{job_id}/retry-requests", status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: int, background_tasks: BackgroundTasks, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    job = db.get(Job, job_id)
    if not job:
        raise not_found("Job")
    job.status = "queued"
    job.progress = 0
    write_audit_log(db, actor_user_id=current_user.id, action="jobs.retry", resource_type="job", resource_id=job.id)
    db.commit()
    background_tasks.add_task(run_local_stub_job, job.id)
    return ok(JobRead.model_validate(job), request.state.request_id)


@router.get("/{job_id}/steps")
def get_job_steps(job_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    steps = list(db.scalars(select(JobStep).where(JobStep.job_id == job_id).order_by(JobStep.id)).all())
    return page([JobStepRead.model_validate(s) for s in steps], 1, len(steps), len(steps), request.state.request_id)


@router.get("/{job_id}/logs")
def get_job_logs(job_id: int, request: Request):
    return ok({"job_id": job_id, "logs": [], "message": "Structured worker logs are not wired in stage 1."}, request.state.request_id)


@router.get("/{job_id}/events")
def get_job_events(job_id: int, request: Request):
    return ok({"job_id": job_id, "events": [], "message": "SSE will be implemented after async queue is introduced."}, request.state.request_id)


@router.get("/{job_id}/results")
def get_job_results(job_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    results = list(db.scalars(select(AnalysisResult).where(AnalysisResult.job_id == job_id).order_by(AnalysisResult.id)).all())
    return page([AnalysisResultRead.model_validate(r) for r in results], 1, len(results), len(results), request.state.request_id)
