from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.jobs.models import AnalysisResult, ReviewRecord
from app.modules.jobs.schemas import ReviewCreate


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
