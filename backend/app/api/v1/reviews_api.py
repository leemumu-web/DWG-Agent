from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import CurrentUser, get_db
from backend.app.models.result import AnalysisResult
from backend.app.schemas.common import page
from backend.app.schemas.result_schema import AnalysisResultRead

router = APIRouter()


@router.get("/pending")
def list_pending_reviews(request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    results = list(db.scalars(select(AnalysisResult).where(AnalysisResult.status == "need_review").order_by(AnalysisResult.id.desc())).all())
    return page([AnalysisResultRead.model_validate(r) for r in results], 1, len(results), len(results), request.state.request_id)
