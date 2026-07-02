from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db
from app.models.result import AnalysisResult
from app.schemas.common import page
from app.schemas.result_schema import AnalysisResultRead

router = APIRouter()


@router.get("/pending")
def list_pending_reviews(request: Request, current_user: CurrentUser, db: Session = Depends(get_db)):
    results = list(db.scalars(select(AnalysisResult).where(AnalysisResult.status == "need_review").order_by(AnalysisResult.id.desc())).all())
    return page([AnalysisResultRead.model_validate(r) for r in results], 1, len(results), len(results), request.state.request_id)
