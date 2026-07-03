from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.result import AnalysisResult, ReviewRecord
from app.schemas.result_schema import ReviewCreate


def get_result_job(db: Session, result: AnalysisResult) -> Job:
    """Resolve the Job that produced this analysis result."""
    from app.core.exceptions import not_found

    job = db.get(Job, result.job_id)
    if not job:
        raise not_found("Job")
    return job


def create_review(
    db: Session, result: AnalysisResult, reviewer_id: int, payload: ReviewCreate
) -> ReviewRecord:
    """Create a review record for an analysis result."""
    review = ReviewRecord(
        result_id=result.id,
        reviewer_id=reviewer_id,
        decision=payload.decision,
        comment=payload.comment,
    )
    db.add(review)
    db.flush()
    return review
