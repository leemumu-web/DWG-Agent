from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.result import AnalysisResult, ReviewRecord
from app.modules.identity.interface import CurrentUser
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import require_project_role
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import not_found
from app.schemas.result_schema import AnalysisResultRead, ReviewCreate, ReviewRead
from app.services.file_service import build_signed_download_url
from app.services.job_access import require_job_read_access, require_job_write_access

router = APIRouter()
PROJECT_REVIEW_ROLES = {"project_owner", "project_reviewer"}


def _get_result_job(db: Session, result: AnalysisResult) -> Job:
    job = db.get(Job, result.job_id)
    if not job:
        raise not_found("Job")
    return job


def _require_result_member(db: Session, current_user: CurrentUser, result: AnalysisResult) -> None:
    job = _get_result_job(db, result)
    require_job_read_access(db, current_user, job)


def _require_result_review_role(
    db: Session, current_user: CurrentUser, result: AnalysisResult
) -> None:
    job = _get_result_job(db, result)
    if job.project_id is not None:
        require_project_role(db, current_user, job.project_id, PROJECT_REVIEW_ROLES)
    else:
        require_job_write_access(db, current_user, job)


@router.get("/{result_id}")
def get_result(
    result_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    _require_result_member(db, current_user, result)
    return ok(AnalysisResultRead.model_validate(result), request.state.request_id)


@router.get("/{result_id}/download-url")
def get_result_download_url(
    result_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    _require_result_member(db, current_user, result)
    if result.result_file_id is None:
        raise not_found("Result file")
    return ok(build_signed_download_url(result.result_file_id), request.state.request_id)


@router.post("/{result_id}/reviews", status_code=status.HTTP_201_CREATED)
def create_review(
    result_id: int,
    payload: ReviewCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    _require_result_review_role(db, current_user, result)
    review = ReviewRecord(
        result_id=result.id,
        reviewer_id=current_user.id,
        decision=payload.decision,
        comment=payload.comment,
    )
    db.add(review)
    db.flush()
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="reviews.create",
        resource_type="result",
        resource_id=result.id,
        after_json=payload.model_dump(),
        request=request,
    )
    db.commit()
    return ok(ReviewRead.model_validate(review), request.state.request_id)


@router.get("/{result_id}/reviews")
def list_result_reviews(
    result_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    _require_result_member(db, current_user, result)
    reviews = [
        ReviewRead.model_validate(r)
        for r in db.scalars(select(ReviewRecord).where(ReviewRecord.result_id == result_id)).all()
    ]
    return ok(reviews, request.state.request_id)
