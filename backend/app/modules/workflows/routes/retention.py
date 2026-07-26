"""Complete Workflow backup and asynchronous retention cleanup routes."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
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
from app.modules.workflows.models import WorkflowRetentionExport
from app.modules.workflows.retention import (
    RETENTION_COOKIE_NAME,
    TERMINAL_WORKFLOW_STATUSES,
    build_retention_scope,
    create_retention_export,
    load_retention_export,
    require_retention_token,
    retention_download_path,
    retention_filename,
    storage_members_for_retention,
    track_retention_stream,
    validate_retention_purge,
)
from app.modules.workflows.schemas import (
    WorkflowRetentionExportRead,
    WorkflowRetentionPreviewRead,
    WorkflowRetentionPurgeRequest,
)
from app.platform.config.settings import settings
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException, forbidden

router = APIRouter()


def _present(
    row: WorkflowRetentionExport,
    *,
    include_download_url: bool,
) -> WorkflowRetentionExportRead:
    return WorkflowRetentionExportRead(
        export_uid=row.export_uid,
        workflow_run_id=row.workflow_run_id,
        status=row.status,
        file_count=row.file_count,
        preview_cache_count=row.preview_cache_count,
        source_size_bytes=row.source_size_bytes,
        reclaimable_size_bytes=row.reclaimable_size_bytes,
        filename=retention_filename(row.workflow_run_id),
        download_url=(
            retention_download_path(row.workflow_run_id, row.export_uid)
            if include_download_url and row.status != "purged" and row.token_digest
            else None
        ),
        token_expires_at=row.token_expires_at,
        downloaded_at=row.downloaded_at,
        task_id=row.task_id,
        purge_started_at=row.purge_started_at,
        purged_at=row.purged_at,
        purged_file_count=row.purged_file_count,
        purged_size_bytes=row.purged_size_bytes,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/{workflow_id}/retention-preview")
def preview_workflow_retention(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    scope = build_retention_scope(db, workflow)
    data = WorkflowRetentionPreviewRead(
        workflow_id=workflow.id,
        workflow_status=workflow.status,
        terminal=workflow.status in TERMINAL_WORKFLOW_STATUSES,
        blocked=bool(scope.blockers),
        blockers=list(scope.blockers),
        file_count=scope.file_count,
        preview_cache_count=scope.preview_cache_count,
        source_size_bytes=scope.source_size_bytes,
        reclaimable_size_bytes=scope.reclaimable_size_bytes,
    )
    return ok(data, request.state.request_id)


@router.post(
    "/{workflow_id}/retention-exports",
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_retention_export(
    workflow_id: int,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id, for_update=True)
    require_project_role(db, current_user, workflow.project_id, WORKFLOW_WRITE_ROLES)
    row, token = create_retention_export(
        db,
        workflow,
        actor_user_id=current_user.id,
        storage=get_storage_backend(),
    )
    download_path = retention_download_path(workflow.id, row.export_uid)
    response.set_cookie(
        RETENTION_COOKIE_NAME,
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
        action="workflow_retention_exports.create",
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={
            "export_uid": row.export_uid,
            "file_count": row.file_count,
            "source_size_bytes": row.source_size_bytes,
            "reclaimable_size_bytes": row.reclaimable_size_bytes,
        },
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ok(_present(row, include_download_url=True), request.state.request_id)


@router.get("/{workflow_id}/retention-exports/latest")
def get_latest_workflow_retention_export(
    workflow_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    statement = select(WorkflowRetentionExport).where(
        WorkflowRetentionExport.workflow_run_id == workflow.id
    )
    if not is_admin(current_user):
        statement = statement.where(WorkflowRetentionExport.created_by == current_user.id)
    row = db.scalar(
        statement.order_by(
            WorkflowRetentionExport.created_at.desc(),
            WorkflowRetentionExport.id.desc(),
        ).limit(1)
    )
    return ok(
        _present(row, include_download_url=True) if row is not None else None,
        request.state.request_id,
    )


@router.get("/{workflow_id}/retention-exports/{export_uid}")
def get_workflow_retention_export(
    workflow_id: int,
    export_uid: str,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    workflow = load_workflow_detail(db, workflow_id)
    require_project_member(db, current_user, workflow.project_id)
    row = load_retention_export(db, workflow.id, export_uid)
    if row.created_by != current_user.id and not is_admin(current_user):
        raise forbidden("只有完整备份创建者或管理员可以查看状态。")
    return ok(_present(row, include_download_url=True), request.state.request_id)


@router.get(
    "/{workflow_id}/retention-exports/{export_uid}/download",
    response_class=StreamingResponse,
)
def download_workflow_retention_export(
    workflow_id: int,
    export_uid: str,
    request: Request,
    db: Session = Depends(get_db),
):
    row = load_retention_export(db, workflow_id, export_uid, for_update=True)
    require_retention_token(row, request.cookies.get(RETENTION_COOKIE_NAME))
    if row.status == "purged":
        raise AppHTTPException(410, "WORKFLOW_RETENTION_PURGED", "本批服务器对象已永久删除。")
    if row.status == "downloading":
        raise AppHTTPException(
            409,
            "WORKFLOW_RETENTION_DOWNLOAD_IN_PROGRESS",
            "完整备份正在下载中，请勿重复点击。",
        )
    if row.status not in {"prepared", "download_failed", "downloaded"}:
        raise AppHTTPException(
            409,
            "WORKFLOW_RETENTION_NOT_DOWNLOADABLE",
            "当前状态不能下载完整备份。",
            {"status": row.status},
        )
    members = storage_members_for_retention(db, row)
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="outbound",
            operation="workflow_retention_export",
            actor_user_id=row.created_by,
            request_id=request.state.request_id,
            idempotency_key=request.state.request_id,
            batch_ref=row.export_uid,
            original_name=retention_filename(workflow_id),
            expected_bytes=row.source_size_bytes,
        ),
    )
    row.status = "downloading"
    row.error_code = None
    row.error_message = None
    db.commit()
    factory = session_factory_for(db)
    chunks = track_retention_stream(
        factory,
        export_uid,
        settle_stream(
            factory,
            transfer.transfer_uid,
            iter_storage_zip(get_storage_backend(), members),
        ),
    )
    encoded = quote(retention_filename(workflow_id))
    return StreamingResponse(
        chunks,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{workflow_id}/retention-exports/{export_uid}/purge",
    status_code=status.HTTP_202_ACCEPTED,
)
def queue_workflow_retention_purge(
    workflow_id: int,
    export_uid: str,
    payload: WorkflowRetentionPurgeRequest,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if not is_admin(current_user):
        raise forbidden("只有管理员可以永久删除完整生产流程的服务器文件。")
    expected_confirmation = f"DELETE WORKFLOW {workflow_id}"
    if payload.confirmation != expected_confirmation:
        raise AppHTTPException(
            409,
            "WORKFLOW_RETENTION_CONFIRMATION_INVALID",
            f"请输入完整确认词：{expected_confirmation}",
        )
    workflow = load_workflow_detail(db, workflow_id, for_update=True)
    row = load_retention_export(db, workflow.id, export_uid, for_update=True)
    validate_retention_purge(db, workflow, row)
    row.status = "purge_queued"
    row.task_id = None
    row.error_code = None
    row.error_message = None
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="workflow_retention_exports.purge_queue",
        resource_type="workflow",
        resource_id=workflow.id,
        after_json={"export_uid": row.export_uid, "file_count": row.file_count},
        request=request,
    )
    db.commit()

    from app.modules.workflows.retention_tasks import purge_workflow_retention_task

    try:
        task = purge_workflow_retention_task.apply_async(
            args=[row.export_uid],
            queue="maintenance",
        )
    except Exception as exc:
        with db.begin():
            failed = load_retention_export(db, workflow.id, export_uid, for_update=True)
            failed.status = "purge_failed"
            failed.error_code = "WORKFLOW_RETENTION_ENQUEUE_FAILED"
            failed.error_message = "维护队列暂不可用，永久删除未开始。"
        raise AppHTTPException(
            503,
            "WORKFLOW_RETENTION_ENQUEUE_FAILED",
            "维护队列暂不可用，服务器文件仍完整保留，请稍后重试。",
        ) from exc
    with db.begin():
        refreshed = load_retention_export(db, workflow.id, export_uid, for_update=True)
        refreshed.task_id = str(task.id)
    db.expire_all()
    refreshed = load_retention_export(db, workflow.id, export_uid)
    return ok(_present(refreshed, include_download_url=False), request.state.request_id)


__all__ = ["router"]
