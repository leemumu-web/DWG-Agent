"""Selective workflow export and user-confirmed permanent cleanup routes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.modules.files.interface import (
    TransferSpec,
    get_storage_backend,
    iter_storage_zip,
    prepare_transfer_in_transaction,
    session_factory_for,
    settle_stream,
)
from app.modules.identity.interface import CurrentUser, is_admin
from app.modules.operations.audit.interface import write_audit_log
from app.modules.projects.interface import require_project_member, require_project_role
from app.modules.workflows.access import WORKFLOW_WRITE_ROLES, load_workflow_detail
from app.modules.workflows.batch_exports import (
    EXPORT_COOKIE_NAME,
    create_export,
    export_download_path,
    export_filename,
    export_preview,
    load_export,
    purge_export,
    require_export_owner,
    require_export_token,
    storage_members_for_download,
    track_export_stream,
)
from app.modules.workflows.models import WorkflowBatchExport
from app.modules.workflows.schemas import (
    WorkflowBatchExportCreate,
    WorkflowBatchExportPreviewRead,
    WorkflowBatchExportPurgeRead,
    WorkflowBatchExportRead,
)
from app.platform.config.settings import settings
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()


def _present_export(
    row: WorkflowBatchExport,
    *,
    include_download_url: bool,
) -> WorkflowBatchExportRead:
    categories = row.categories_json if isinstance(row.categories_json, list) else []
    download_url = (
        export_download_path(row.workflow_run_id, row.export_uid)
        if include_download_url and row.status != "purged" and row.token_digest
        else None
    )
    return WorkflowBatchExportRead(
        export_uid=row.export_uid,
        workflow_run_id=row.workflow_run_id,
        status=row.status,
        categories=categories,
        file_count=row.file_count,
        source_size_bytes=row.source_size_bytes,
        filename=export_filename(row.workflow_run_id, categories),
        download_url=download_url,
        token_expires_at=row.token_expires_at,
        downloaded_at=row.downloaded_at,
        purged_at=row.purged_at,
        purged_file_count=row.purged_file_count,
        purged_size_bytes=row.purged_size_bytes,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get(
    "/{workflow_id}/batch-exports/preview",
    summary="预览工作流分批导出",
    description="按四类固定展示文案统计当前可导出的登记文件，不读取对象内容。",
)
def preview_workflow_batch_export(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    data = WorkflowBatchExportPreviewRead(
        workflow_id=workflow.id,
        categories=export_preview(db, workflow),
    )
    return ok(data, request.state.request_id)


@router.post(
    "/{workflow_id}/batch-exports",
    status_code=status.HTTP_201_CREATED,
    summary="创建工作流分批导出",
    description=(
        "冻结所选文件的数据库清单并签发仅能访问本次下载路径的短期 HttpOnly 能力；"
        "ZIP 在响应过程中直接从对象存储流向浏览器。"
    ),
)
def create_workflow_batch_export(
    workflow_id: int,
    payload: WorkflowBatchExportCreate,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    row, token = create_export(
        db,
        workflow,
        categories=list(payload.categories),
        actor_user_id=current_user.id,
    )
    download_path = export_download_path(workflow.id, row.export_uid)
    response.set_cookie(
        EXPORT_COOKIE_NAME,
        token,
        max_age=settings.workflow_batch_export_ttl_minutes * 60,
        httponly=True,
        secure=settings.refresh_cookie_secure_enabled,
        samesite="lax",
        path=download_path,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_batch_exports.create",
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={
            "export_uid": row.export_uid,
            "categories": row.categories_json,
            "file_count": row.file_count,
            "source_size_bytes": row.source_size_bytes,
        },
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ok(_present_export(row, include_download_url=True), request.state.request_id)


@router.get(
    "/{workflow_id}/batch-exports/{export_uid}",
    summary="读取工作流分批导出状态",
)
def get_workflow_batch_export(
    workflow_id: int,
    export_uid: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    row = load_export(db, workflow.id, export_uid)
    require_export_owner(
        row,
        actor_user_id=current_user.id,
        actor_is_admin=is_admin(current_user),
    )
    return ok(_present_export(row, include_download_url=True), request.state.request_id)


@router.get(
    "/{workflow_id}/batch-exports/{export_uid}/download",
    summary="流式下载工作流分批导出",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {
                "application/zip": {
                    "schema": {"type": "string", "format": "binary"},
                }
            }
        }
    },
    description=(
        "使用创建接口写入的路径级 HttpOnly 能力校验下载；不生成服务器临时 ZIP，"
        "也不在此步骤删除任何源文件。"
    ),
)
def download_workflow_batch_export(
    workflow_id: int,
    export_uid: str,
    request: Request,
    db: Session = Depends(get_db),
):
    row = load_export(db, workflow_id, export_uid, for_update=True)
    require_export_token(row, request.cookies.get(EXPORT_COOKIE_NAME))
    if row.status == "purged":
        raise AppHTTPException(
            410,
            "WORKFLOW_EXPORT_PURGED",
            "本次导出的服务器文件已永久删除。",
        )
    if row.status == "downloading":
        raise AppHTTPException(
            409,
            "WORKFLOW_EXPORT_DOWNLOAD_IN_PROGRESS",
            "本次导出正在下载中。",
        )
    if row.status not in {"prepared", "download_failed", "downloaded"}:
        raise AppHTTPException(
            409,
            "WORKFLOW_EXPORT_NOT_DOWNLOADABLE",
            "本次导出当前不可下载。",
            {"status": row.status},
        )

    members = storage_members_for_download(db, row)
    storage = get_storage_backend()
    row.status = "downloading"
    row.error_code = None
    row.error_message = None
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="outbound",
            operation="workflow_batch_export",
            actor_user_id=row.created_by,
            request_id=request.state.request_id,
            idempotency_key=request.state.request_id,
            batch_ref=row.export_uid,
            original_name=export_filename(
                workflow_id,
                row.categories_json
                if isinstance(row.categories_json, list)
                else [],
            ),
        ),
    )
    write_audit_log(
        db,
        actor_user_id=row.created_by,
        action="workflow_batch_exports.download",
        resource_type="workflow",
        resource_id=workflow_id,
        after_json={
            "export_uid": row.export_uid,
            "categories": row.categories_json,
            "file_count": row.file_count,
        },
        request=request,
    )
    db.commit()

    factory = session_factory_for(db)
    chunks = track_export_stream(
        factory,
        export_uid,
        settle_stream(
            factory,
            transfer.transfer_uid,
            iter_storage_zip(storage, members),
        ),
    )
    encoded_filename = quote(
        export_filename(
            workflow_id,
            row.categories_json
            if isinstance(row.categories_json, list)
            else [],
        )
    )
    return StreamingResponse(
        chunks,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{workflow_id}/batch-exports/{export_uid}/purge",
    summary="确认并永久删除已导出的服务器文件",
    description=(
        "仅在服务端确认 ZIP 响应完整结束后执行。物理删除所选 MinIO/本地对象及其 DXF "
        "预览缓存，并保留带 purged_at 的文件登记墓碑以维持生产记录引用。"
    ),
)
def purge_workflow_batch_export(
    workflow_id: int,
    export_uid: str,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id, for_update=True)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    row = load_export(db, workflow.id, export_uid, for_update=True)
    require_export_owner(
        row,
        actor_user_id=current_user.id,
        actor_is_admin=is_admin(current_user),
    )
    before = {
        "status": row.status,
        "categories": row.categories_json,
        "file_count": row.file_count,
        "source_size_bytes": row.source_size_bytes,
    }
    try:
        purged_file_count, released_bytes = purge_export(
            db,
            workflow,
            row,
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
        )
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="workflow_batch_exports.purge",
            resource_type="workflow",
            resource_id=workflow.id,
            before_json=before,
            after_json={
                "export_uid": row.export_uid,
                "status": row.status,
                "purged_file_count": purged_file_count,
                "released_bytes": released_bytes,
            },
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    response.delete_cookie(
        EXPORT_COOKIE_NAME,
        path=export_download_path(workflow.id, row.export_uid),
    )
    data = WorkflowBatchExportPurgeRead(
        export_uid=row.export_uid,
        status="purged",
        purged_file_count=purged_file_count,
        released_bytes=released_bytes,
    )
    return ok(data, request.state.request_id)


__all__ = ["router"]
