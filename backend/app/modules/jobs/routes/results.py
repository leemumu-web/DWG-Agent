"""Job Result listing and Result item retrieval."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.interface import build_signed_download_url
from app.modules.identity.interface import CurrentUser
from app.modules.jobs.access import require_job_read_access, require_result_read_access
from app.modules.jobs.models import AnalysisResult, Job
from app.modules.jobs.schemas import AnalysisResultRead
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import not_found

job_router = APIRouter()
result_router = APIRouter()


@job_router.get("/{job_id}/results")
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
    require_job_read_access(db, current_user, job)
    results, total = paginate_scalars(
        db,
        select(AnalysisResult).where(AnalysisResult.job_id == job_id).order_by(AnalysisResult.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [AnalysisResultRead.model_validate(r) for r in results],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@result_router.get("/{result_id}")
def get_result(
    result_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    require_result_read_access(db, current_user, result)
    return ok(AnalysisResultRead.model_validate(result), request.state.request_id)


@result_router.get("/{result_id}/download-url")
def get_result_download_url(
    result_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    result = db.get(AnalysisResult, result_id)
    if not result:
        raise not_found("Result")
    require_result_read_access(db, current_user, result)
    if result.result_file_id is None:
        raise not_found("Result file")
    return ok(build_signed_download_url(result.result_file_id), request.state.request_id)
