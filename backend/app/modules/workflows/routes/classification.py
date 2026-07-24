"""DXF classification ledger projection for a workflow."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.modules.dxf_classification.interface import (
    build_classification_group_page,
    build_classification_run_read,
    latest_classification_run,
)
from app.modules.identity.interface import CurrentUser
from app.modules.projects.interface import require_project_member
from app.modules.workflows.access import load_workflow_detail
from app.modules.workflows.job_sync import sync_workflow_from_jobs
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()


@router.get(
    "/{workflow_id}/dxf-classification",
    summary="读取最新 DXF 分类分流账本",
    description="返回分类 Job、版本、汇总、逐图来源/输出登记和 JSON/CSV 报告文件。",
)
def get_dxf_classification(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    sync_workflow_from_jobs(db, workflow)
    run = latest_classification_run(db, workflow.id)
    db.commit()
    if run is None:
        return ok(None, request.state.request_id)
    payload = build_classification_run_read(db, run)
    return ok(payload, request.state.request_id)


@router.get(
    "/{workflow_id}/dxf-classification/groups/{group_key}",
    summary="读取 DXF 分类文件夹明细",
    description="分页返回一个分类组中的 DXF 文件语义，不暴露内部文件标识或审计文件。",
)
def get_dxf_classification_group(
    workflow_id: int,
    group_key: str,
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    sync_workflow_from_jobs(db, workflow)
    run = latest_classification_run(db, workflow.id)
    db.commit()
    if run is None:
        raise AppHTTPException(
            404,
            "CLASSIFICATION_RUN_NOT_FOUND",
            "No DXF classification run exists for this workflow.",
        )
    payload = build_classification_group_page(
        db,
        run,
        group_key=group_key,
        page=page,
        page_size=page_size,
    )
    return ok(payload, request.state.request_id)
