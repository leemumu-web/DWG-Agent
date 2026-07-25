"""DXF split run projection and manual-review source archive."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.modules.dxf_splitting.interface import (
    build_dxf_split_run_read,
    latest_dxf_split_run,
    manual_review_archive_members,
)
from app.modules.identity.interface import CurrentUser
from app.modules.projects.interface import require_project_member
from app.modules.workflows.access import load_workflow_detail
from app.modules.workflows.job_sync import sync_workflow_from_jobs
from app.modules.workflows.routes.archive import stream_registered_workflow_archive
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()


def _is_current_drawing_attempt(workflow, run) -> bool:
    stage = next(
        (item for item in workflow.stages if item.stage_code == "drawing_processing"),
        None,
    )
    return bool(
        stage is not None
        and run is not None
        and stage.job_id == run.job_id
        and stage.job_attempt == run.job_attempt
    )


@router.get(
    "/{workflow_id}/drawing-processing",
    summary="读取当前拆板批次",
    description="只返回工作流当前 Job attempt 的拆板、独立校验和人工复核汇总。",
)
def get_drawing_processing(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    sync_workflow_from_jobs(db, workflow)
    run = latest_dxf_split_run(db, workflow.id)
    db.commit()
    if not _is_current_drawing_attempt(workflow, run):
        return ok(None, request.state.request_id)
    return ok(build_dxf_split_run_read(db, run), request.state.request_id)


@router.get(
    "/{workflow_id}/drawing-processing/runs/{run_id}/manual-review-archive",
    summary="下载本批次未通过原图",
    response_class=StreamingResponse,
    description=(
        "即时生成 ZIP，只包含当前拆板 attempt 中进入 manual_review 的分类原始 DXF；"
        "不包含候选图、报告、预览或 Excel。"
    ),
)
def download_manual_review_archive(
    workflow_id: int,
    run_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    run = latest_dxf_split_run(db, workflow.id)
    if not _is_current_drawing_attempt(workflow, run) or run is None or run.id != run_id:
        raise AppHTTPException(
            404,
            "DXF_SPLIT_RUN_NOT_CURRENT",
            "请求的拆板批次不是工作流当前 attempt。",
        )
    if run.status != "completed_with_review":
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_ARCHIVE_UNAVAILABLE",
            "当前拆板批次没有待人工处理图纸。",
            {"split_run_id": run.id, "status": run.status},
        )
    members = manual_review_archive_members(db, run)
    if not members:
        raise AppHTTPException(
            409,
            "DXF_SPLIT_REVIEW_ARCHIVE_EMPTY",
            "当前拆板批次没有可下载的未通过原图。",
        )
    # Project membership plus current run/attempt lineage is the authority here.
    # Classified outputs are server-generated and may have a different uploader.
    return stream_registered_workflow_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}-split-run-{run.id}-manual-review",
        operation="dxf_split_manual_review_zip",
        audit_action="dxf_split_manual_review_archives.download",
    )
