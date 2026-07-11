from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, has_global_project_access
from app.db.pagination import paginate_scalars
from app.models.job import Job
from app.models.project import ProjectMember
from app.models.result import AnalysisResult
from app.schemas.common import page as page_response
from app.schemas.result_schema import AnalysisResultRead

router = APIRouter()


@router.get("/pending")
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
