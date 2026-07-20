"""Result Review commands, history and pending queue."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.interface import CurrentUser
from app.modules.jobs.access import require_result_read_access, require_result_review_access
from app.modules.jobs.models import AnalysisResult, Job, ReviewRecord
from app.modules.jobs.reviews import create_review as create_review_record
from app.modules.jobs.schemas import AnalysisResultRead, ReviewCreate, ReviewRead
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import ProjectMember, has_global_project_access
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import not_found

result_router = APIRouter()
review_router = APIRouter()


@result_router.post("/{result_id}/reviews", status_code=status.HTTP_201_CREATED)
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
    require_result_review_access(db, current_user, result)
    review = create_review_record(db, result, current_user.id, payload)
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


@result_router.get("/{result_id}/reviews")
def list_result_reviews(
    result_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    require_result_read_access(db, current_user, result)
    reviews = [
        ReviewRead.model_validate(r)
        for r in db.scalars(select(ReviewRecord).where(ReviewRecord.result_id == result_id)).all()
    ]
    return ok(reviews, request.state.request_id)


@review_router.get("/pending")
def list_pending_reviews(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    stmt = (
        select(AnalysisResult)
        .join(Job, Job.id == AnalysisResult.job_id)
        .where(AnalysisResult.status == "need_review")
        .order_by(AnalysisResult.id.desc())
    )
    if not has_global_project_access(current_user):
        stmt = stmt.join(ProjectMember, ProjectMember.project_id == Job.project_id).where(
            ProjectMember.user_id == current_user.id
        )
    results, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    return page_response(
        [AnalysisResultRead.model_validate(r) for r in results],
        page,
        page_size,
        total,
        request.state.request_id,
    )
