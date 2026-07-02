from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db
from app.core.exceptions import not_found
from app.models.result import AnalysisResult, ReviewRecord
from app.schemas.common import ok
from app.schemas.file_schema import DownloadUrlRead
from app.schemas.result_schema import AnalysisResultRead, ReviewCreate, ReviewRead
from app.services.audit_service import write_audit_log

router = APIRouter()


@router.get("/{result_id}")
def get_result(result_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    return ok(AnalysisResultRead.model_validate(result), request.state.request_id)


@router.get("/{result_id}/download-url")
def get_result_download_url(result_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    return ok(DownloadUrlRead(url=f"/api/v1/files/{result.result_file_id}/download", expires_in=300), request.state.request_id)


@router.post("/{result_id}/reviews", status_code=status.HTTP_201_CREATED)
def create_review(result_id: int, payload: ReviewCreate, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    review = ReviewRecord(result_id=result.id, reviewer_id=current_user.id, decision=payload.decision, comment=payload.comment)
    db.add(review)
    db.flush()
    write_audit_log(db, actor_user_id=current_user.id, action="reviews.create", resource_type="result", resource_id=result.id, after_json=payload.model_dump())
    db.commit()
    return ok(ReviewRead.model_validate(review), request.state.request_id)


@router.get("/{result_id}/reviews")
def list_result_reviews(result_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    reviews = [ReviewRead.model_validate(r) for r in db.scalars(select(ReviewRecord).where(ReviewRecord.result_id == result_id)).all()]
    return ok(reviews, request.state.request_id)
