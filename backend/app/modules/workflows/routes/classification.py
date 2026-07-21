"""DXF classification ledger projection for a workflow."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.modules.dxf_classification.interface import (
    build_classification_run_read,
    latest_classification_run,
)
from app.modules.identity.interface import CurrentUser
from app.modules.projects.interface import require_project_member
from app.modules.workflows.access import load_workflow_detail
from app.modules.workflows.job_sync import sync_workflow_from_jobs
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok

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
