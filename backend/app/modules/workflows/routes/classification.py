"""DXF classification ledger projection for a workflow."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.modules.dxf_classification.interface import (
    build_classification_group_page,
    build_classification_run_read,
    latest_classification_run,
)
from app.modules.files.interface import (
    StoredFile,
    require_file_read_access,
    sanitize_filename,
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


def _classification_group_label(group_key: str, part_type: str | None) -> str:
    if group_key == "status:review_required":
        return "待确认"
    if group_key == "status:unreadable":
        return "无法读取"
    if group_key.startswith("type:"):
        return part_type or group_key.removeprefix("type:")
    return group_key


def _classification_archive_members(
    db: Session,
    current_user: CurrentUser,
    run,
    *,
    group_key: str | None = None,
) -> tuple[list[tuple[int, str]], str | None]:
    items = [
        item
        for item in run.items
        if group_key is None or item.group_key == group_key
    ]
    if group_key is not None and not items:
        raise AppHTTPException(
            404,
            "CLASSIFICATION_GROUP_NOT_FOUND",
            "The DXF classification group was not found.",
            {"group_key": group_key},
        )
    if not items:
        raise AppHTTPException(
            409,
            "CLASSIFICATION_ARCHIVE_EMPTY",
            "The DXF classification run has no downloadable outputs.",
        )

    project_name = sanitize_filename(run.project_name)
    members: list[tuple[int, str]] = []
    seen_paths: set[str] = set()
    selected_label: str | None = None
    for item in items:
        stored = db.get(StoredFile, item.output_file_id)
        if (
            stored is None
            or stored.status == "deleted"
            or stored.file_ext.lower() != ".dxf"
        ):
            raise AppHTTPException(
                409,
                "CLASSIFICATION_OUTPUT_MISSING",
                "A classified DXF output is unavailable.",
                {"group_key": item.group_key},
            )
        require_file_read_access(db, current_user, stored)
        label = _classification_group_label(item.group_key, item.part_type)
        if group_key is not None:
            selected_label = label
        relative_path = (
            f"{project_name}/{sanitize_filename(label)}/"
            f"{sanitize_filename(item.output_name)}"
        )
        if relative_path.casefold() in seen_paths:
            relative_path = (
                f"{project_name}/{sanitize_filename(label)}/"
                f"{stored.id}-{sanitize_filename(item.output_name)}"
            )
        seen_paths.add(relative_path.casefold())
        members.append((stored.id, relative_path))
    return members, selected_label


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


@router.get(
    "/{workflow_id}/dxf-classification/groups/{group_key}/download-archive",
    summary="下载一个 DXF 分类文件夹",
    response_class=StreamingResponse,
    description="只打包指定分类组的正式 DXF，不包含 JSON、CSV、DWG 或其他阶段产物。",
)
def download_dxf_classification_group_archive(
    workflow_id: int,
    group_key: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    run = latest_classification_run(db, workflow.id)
    if run is None:
        raise AppHTTPException(
            404,
            "CLASSIFICATION_RUN_NOT_FOUND",
            "No DXF classification run exists for this workflow.",
        )
    members, label = _classification_archive_members(
        db,
        current_user,
        run,
        group_key=group_key,
    )
    return stream_registered_workflow_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}-dxf-{sanitize_filename(label or 'group')}",
        operation="dxf_class_group_zip",
        audit_action="dxf_classification_groups.download",
    )


@router.get(
    "/{workflow_id}/dxf-classification/download-archive",
    summary="下载全部分类 DXF",
    response_class=StreamingResponse,
    description="按分类文件夹打包本次运行的全部正式 DXF，不包含 JSON、CSV 或其他产物。",
)
def download_all_dxf_classification_archive(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    run = latest_classification_run(db, workflow.id)
    if run is None:
        raise AppHTTPException(
            404,
            "CLASSIFICATION_RUN_NOT_FOUND",
            "No DXF classification run exists for this workflow.",
        )
    members, _ = _classification_archive_members(db, current_user, run)
    return stream_registered_workflow_archive(
        db,
        request,
        current_user,
        workflow,
        members,
        f"workflow-{workflow.id}-all-classified-dxf",
        operation="dxf_class_all_zip",
        audit_action="dxf_classification_archives.download",
    )
