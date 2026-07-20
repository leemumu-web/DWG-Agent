from __future__ import annotations

from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select

import app.modules.files.interface as storage_service
from app.models.daily_archive import DailyArchiveRun
from app.modules.files.interface import (
    FileRead,
    FileTransfer,
    StorageScanFinding,
    StorageScanRun,
    StoredFile,
)
from app.modules.identity.interface import require_roles
from app.modules.operations.audit.interface import write_audit_log
from app.platform.config.constants import ROLE_ADMIN, ROLE_AUDITOR
from app.platform.config.settings import settings
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import DbSession
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found
from app.platform.storage.base import StorageError
from app.services.daily_archive_service import (
    daily_archive_run_data,
    prepare_daily_archive_run,
    preview_daily_archive,
)
from app.services.storage_reconciliation_service import (
    execute_remediation,
    preview_remediation,
)
from app.workers.tasks_report import scan_storage_consistency_task

router = APIRouter()
data_reader = require_roles(ROLE_ADMIN, ROLE_AUDITOR)
data_writer = require_roles(ROLE_ADMIN)


class ScanCreateRequest(BaseModel):
    scope_bucket: str | None = None


class RemediationPreviewRequest(BaseModel):
    finding_ids: list[int]
    action: str
    metadata: dict[str, str] | None = None


class RemediationExecuteRequest(BaseModel):
    preview_token: str
    idempotency_key: str
    confirmation_word: str | None = None


class DailyArchivePreviewRequest(BaseModel):
    archive_date: date | None = None
    scope_bucket: str | None = None


class DailyArchiveCreateRequest(BaseModel):
    preview_token: str
    idempotency_key: str


def _transfer_data(row: FileTransfer) -> dict:
    return {
        "transfer_uid": row.transfer_uid,
        "direction": row.direction,
        "operation": row.operation,
        "status": row.status,
        "file_id": row.file_id,
        "batch_ref": row.batch_ref,
        "actor_user_id": row.actor_user_id,
        "request_id": row.request_id,
        "bucket": row.bucket,
        "storage_key": row.storage_key,
        "original_name": row.original_name,
        "expected_bytes": row.expected_bytes,
        "transferred_bytes": row.transferred_bytes,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "created_at": row.created_at,
    }


def _scan_data(row: StorageScanRun) -> dict:
    return {
        "id": row.id,
        "backend": row.backend,
        "scope_bucket": row.scope_bucket,
        "status": row.status,
        "actor_user_id": row.actor_user_id,
        "scanned_files": row.scanned_files,
        "scanned_objects": row.scanned_objects,
        "consistent_count": row.consistent_count,
        "retained_deleted_count": row.retained_deleted_count,
        "missing_object_count": row.missing_object_count,
        "untracked_object_count": row.untracked_object_count,
        "size_mismatch_count": row.size_mismatch_count,
        "error_count": row.error_count,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "created_at": row.created_at,
    }


@router.post("/daily-archives/preview")
def preview_daily_archive_run(
    payload: DailyArchivePreviewRequest,
    request: Request,
    db: DbSession,
    current_user=Depends(data_reader),
):
    preview = preview_daily_archive(
        db,
        archive_date=payload.archive_date,
        scope_bucket=payload.scope_bucket,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="daily_archive.preview",
        resource_type="daily_archive_run",
        after_json={
            "archive_date": preview.archive_date.isoformat(),
            "scope_bucket": preview.scope_bucket,
            "file_count": preview.file_count,
            "total_bytes": preview.total_bytes,
            "source_manifest_sha256": preview.source_manifest_sha256,
            "can_archive": preview.can_archive,
        },
        request=request,
    )
    db.commit()
    return ok(preview.model_dump(mode="json"), request.state.request_id)


@router.post("/daily-archives", status_code=202)
def create_daily_archive_run(
    payload: DailyArchiveCreateRequest,
    request: Request,
    db: DbSession,
    current_user=Depends(data_writer),
):
    idempotency_key = payload.idempotency_key.strip()
    if not idempotency_key or len(idempotency_key) > 128:
        raise AppHTTPException(
            422,
            "IDEMPOTENCY_KEY_INVALID",
            "Idempotency key must contain 1 to 128 characters.",
        )
    row, reused = prepare_daily_archive_run(
        db,
        actor_user_id=current_user.id,
        preview_token=payload.preview_token,
        idempotency_key=idempotency_key,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="daily_archive.reuse" if reused else "daily_archive.queue",
        resource_type="daily_archive_run",
        resource_id=row.id,
        after_json={
            "archive_date": row.archive_date.isoformat(),
            "scope_bucket": row.scope_bucket,
            "file_count": row.file_count,
            "source_manifest_sha256": row.source_manifest_sha256,
            "reused": reused,
        },
        request=request,
    )
    run_id = row.id
    db.commit()
    if not reused:
        from app.workers.tasks_maintenance import create_daily_archive_task

        try:
            result = create_daily_archive_task.apply_async(args=[run_id], queue="maintenance")
        except Exception as exc:
            with db.begin():
                failed = db.get(DailyArchiveRun, run_id, populate_existing=True)
                if failed is not None and failed.status in {"queued", "running"}:
                    failed.status = "failed"
                    failed.error_code = "DAILY_ARCHIVE_ENQUEUE_FAILED"
                    failed.error_message = "维护队列暂不可用，归档未开始。"
                    failed.finished_at = datetime.now(UTC)
            raise AppHTTPException(
                503,
                "DAILY_ARCHIVE_ENQUEUE_FAILED",
                "Daily archive could not be queued.",
            ) from exc
        with db.begin():
            refreshed = db.get(DailyArchiveRun, run_id, populate_existing=True)
            if refreshed is not None:
                refreshed.task_id = result.id
    db.expire_all()
    refreshed = db.get(DailyArchiveRun, run_id)
    if refreshed is None:
        raise not_found("DailyArchiveRun")
    return ok(
        daily_archive_run_data(refreshed, reused=reused).model_dump(mode="json"),
        request.state.request_id,
    )


@router.get("/daily-archives")
def list_daily_archive_runs(
    request: Request,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(default=None),
    scope_bucket: str | None = Query(default=None),
    archive_date: date | None = Query(default=None),
    _current_user=Depends(data_reader),
):
    statement = select(DailyArchiveRun)
    if status:
        statement = statement.where(DailyArchiveRun.status == status)
    if scope_bucket:
        statement = statement.where(DailyArchiveRun.scope_bucket == scope_bucket)
    if archive_date:
        statement = statement.where(DailyArchiveRun.archive_date == archive_date)
    rows, total = paginate_scalars(
        db,
        statement.order_by(DailyArchiveRun.id.desc()),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [daily_archive_run_data(row).model_dump(mode="json") for row in rows],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.get("/daily-archives/{archive_id}")
def get_daily_archive_run(
    archive_id: int,
    request: Request,
    db: DbSession,
    _current_user=Depends(data_reader),
):
    row = db.get(DailyArchiveRun, archive_id)
    if row is None:
        raise not_found("DailyArchiveRun")
    return ok(
        daily_archive_run_data(row).model_dump(mode="json"),
        request.state.request_id,
    )


@router.get("/overview")
def get_data_overview(
    request: Request,
    db: DbSession,
    _current_user=Depends(data_reader),
):
    counts = dict(
        db.execute(
            select(StoredFile.status, func.count(StoredFile.id)).group_by(StoredFile.status)
        ).all()
    )
    tracked_bytes = db.scalar(
        select(func.coalesce(func.sum(StoredFile.size_bytes), 0)).where(
            StoredFile.status == "available"
        )
    )
    latest_scan = db.scalar(select(StorageScanRun).order_by(StorageScanRun.id.desc()).limit(1))
    today_start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    transfer_counts = {
        (direction, status): int(count)
        for direction, status, count in db.execute(
            select(
                FileTransfer.direction,
                FileTransfer.status,
                func.count(FileTransfer.id),
            )
            .where(FileTransfer.created_at >= today_start)
            .group_by(FileTransfer.direction, FileTransfer.status)
        ).all()
    }
    storage_status = "ok"
    try:
        storage_service.get_storage_backend().check_health()
    except StorageError:
        storage_status = "error"

    data = {
        "status": "ok" if storage_status == "ok" else "degraded",
        "environment": {
            "app_env": settings.app_env,
            "database_engine": db.get_bind().dialect.name,
            "database": settings.mysql_database,
            "storage_backend": settings.storage_backend,
        },
        "database": {"status": "ok"},
        "storage": {"status": storage_status},
        "catalog": {
            "available_files": int(counts.get("available", 0)),
            "deleted_files": int(counts.get("deleted", 0)),
            "tracked_bytes": int(tracked_bytes or 0),
        },
        "transfers_today": {
            "inbound_succeeded": transfer_counts.get(("inbound", "succeeded"), 0),
            "outbound_succeeded": transfer_counts.get(("outbound", "succeeded"), 0),
            "attention_required": sum(
                count
                for (_direction, status), count in transfer_counts.items()
                if status in {"failed", "compensation_required"}
            ),
        },
        "latest_scan": (
            {
                "id": latest_scan.id,
                "status": latest_scan.status,
                "finished_at": latest_scan.finished_at,
                "missing_object_count": latest_scan.missing_object_count,
                "untracked_object_count": latest_scan.untracked_object_count,
            }
            if latest_scan
            else None
        ),
    }
    return ok(data, request.state.request_id)


@router.get("/files")
def list_data_files(
    request: Request,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: str = Query("", max_length=255),
    status: str | None = Query(default=None),
    bucket: str | None = Query(default=None),
    file_ext: str | None = Query(default=None),
    _current_user=Depends(data_reader),
):
    statement = select(StoredFile)
    if search.strip():
        term = f"%{search.strip()}%"
        conditions = [
            StoredFile.original_name.ilike(term),
            StoredFile.sha256.ilike(term),
        ]
        if search.strip().isdigit():
            conditions.append(StoredFile.id == int(search.strip()))
        statement = statement.where(or_(*conditions))
    if status:
        statement = statement.where(StoredFile.status == status)
    if bucket:
        statement = statement.where(StoredFile.bucket == bucket)
    if file_ext:
        statement = statement.where(StoredFile.file_ext == file_ext)
    rows, total = paginate_scalars(
        db,
        statement.order_by(StoredFile.id.desc()),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [FileRead.model_validate(row) for row in rows],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.get("/files/{file_id}")
def get_data_file(
    file_id: int,
    request: Request,
    db: DbSession,
    _current_user=Depends(data_reader),
):
    row = db.get(StoredFile, file_id)
    if row is None:
        raise not_found("StoredFile")
    return ok(FileRead.model_validate(row), request.state.request_id)


@router.get("/objects")
def list_storage_objects(
    request: Request,
    db: DbSession,
    bucket: str = Query(...),
    prefix: str = Query("", max_length=512),
    cursor: str | None = Query(default=None, max_length=512),
    page_size: int = Query(50, ge=1, le=200),
    _current_user=Depends(data_reader),
):
    if bucket not in settings.minio_bucket_names:
        raise AppHTTPException(422, "INVALID_BUCKET", "Bucket is not configured.")
    # Authentication/RBAC performs read queries on this session. End that
    # transaction before potentially slow MinIO/local filesystem enumeration so
    # object listing never occupies one of the small API MySQL pool slots.
    db.rollback()
    try:
        object_page = storage_service.get_storage_backend().list_objects(
            bucket,
            prefix=prefix,
            cursor=cursor,
            page_size=page_size,
        )
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "STORAGE_LIST_FAILED",
            "Storage objects could not be listed.",
        ) from exc

    keys = [item.storage_key for item in object_page.items]
    registered = {
        row.storage_key: row
        for row in db.scalars(
            select(StoredFile).where(
                StoredFile.bucket == bucket,
                StoredFile.storage_key.in_(keys),
            )
        ).all()
    } if keys else {}
    items = [
        {
            "bucket": item.bucket,
            "storage_key": item.storage_key,
            "size_bytes": item.size_bytes,
            "last_modified": item.last_modified,
            "registered": item.storage_key in registered,
            "file_id": registered[item.storage_key].id if item.storage_key in registered else None,
            "file_status": (
                registered[item.storage_key].status if item.storage_key in registered else None
            ),
        }
        for item in object_page.items
    ]
    response = ok(items, request.state.request_id)
    response["cursor"] = {"next": object_page.next_cursor}
    return response


@router.get("/transfers")
def list_transfers(
    request: Request,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    direction: str | None = Query(default=None),
    status: str | None = Query(default=None),
    operation: str | None = Query(default=None),
    file_id: int | None = Query(default=None),
    _current_user=Depends(data_reader),
):
    statement = select(FileTransfer)
    if direction:
        statement = statement.where(FileTransfer.direction == direction)
    if status:
        statement = statement.where(FileTransfer.status == status)
    if operation:
        statement = statement.where(FileTransfer.operation == operation)
    if file_id is not None:
        statement = statement.where(FileTransfer.file_id == file_id)
    rows, total = paginate_scalars(
        db,
        statement.order_by(FileTransfer.id.desc()),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [_transfer_data(row) for row in rows],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.get("/transfers/{transfer_uid}")
def get_transfer(
    transfer_uid: str,
    request: Request,
    db: DbSession,
    _current_user=Depends(data_reader),
):
    row = db.scalar(
        select(FileTransfer).where(FileTransfer.transfer_uid == transfer_uid)
    )
    if row is None:
        raise not_found("FileTransfer")
    return ok(_transfer_data(row), request.state.request_id)


@router.post("/scans", status_code=202)
def start_scan(
    payload: ScanCreateRequest,
    request: Request,
    db: DbSession,
    current_user=Depends(data_writer),
):
    if payload.scope_bucket and payload.scope_bucket not in settings.minio_bucket_names:
        raise AppHTTPException(422, "INVALID_BUCKET", "Bucket is not configured.")
    scope_condition = (
        StorageScanRun.scope_bucket == payload.scope_bucket
        if payload.scope_bucket is not None
        else StorageScanRun.scope_bucket.is_(None)
    )
    active = db.scalar(
        select(StorageScanRun).where(
            scope_condition,
            StorageScanRun.status.in_(("queued", "running")),
        )
    )
    if active is not None:
        raise AppHTTPException(
            409,
            "CONSISTENCY_SCAN_ACTIVE",
            "A consistency scan is already active for this scope.",
            {"scan_id": active.id},
        )
    row = StorageScanRun(
        backend=settings.storage_backend,
        scope_bucket=payload.scope_bucket,
        status="queued",
        actor_user_id=current_user.id,
    )
    db.add(row)
    db.flush()
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="storage.scan_queue",
        resource_type="storage_scan_run",
        resource_id=row.id,
        after_json={"scope_bucket": payload.scope_bucket, "backend": row.backend},
        request=request,
    )
    db.commit()
    scan_storage_consistency_task.delay(row.id)
    db.expire_all()
    refreshed = db.get(StorageScanRun, row.id) or row
    return ok(_scan_data(refreshed), request.state.request_id)


@router.get("/scans")
def list_scans(
    request: Request,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    status: str | None = Query(default=None),
    scope_bucket: str | None = Query(default=None),
    _current_user=Depends(data_reader),
):
    statement = select(StorageScanRun)
    if status:
        statement = statement.where(StorageScanRun.status == status)
    if scope_bucket:
        statement = statement.where(StorageScanRun.scope_bucket == scope_bucket)
    rows, total = paginate_scalars(
        db,
        statement.order_by(StorageScanRun.id.desc()),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [_scan_data(row) for row in rows],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.post("/remediations/preview")
def preview_storage_remediation(
    payload: RemediationPreviewRequest,
    request: Request,
    db: DbSession,
    current_user=Depends(data_reader),
):
    preview = preview_remediation(
        db,
        storage_service.get_storage_backend(),
        actor_user_id=current_user.id,
        finding_ids=payload.finding_ids,
        action=payload.action,
        metadata=payload.metadata,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="storage.remediation_preview",
        resource_type="storage_scan_finding",
        after_json={
            "action": preview.action,
            "count": preview.count,
            "total_bytes": preview.total_bytes,
            "finding_ids": preview.finding_ids,
        },
        request=request,
    )
    db.commit()
    return ok(preview.model_dump(mode="json"), request.state.request_id)


@router.post("/remediations/execute")
def execute_storage_remediation(
    payload: RemediationExecuteRequest,
    request: Request,
    db: DbSession,
    current_user=Depends(data_writer),
):
    result = execute_remediation(
        db,
        storage_service.get_storage_backend(),
        actor_user_id=current_user.id,
        preview_token=payload.preview_token,
        idempotency_key=payload.idempotency_key,
        request_id=request.state.request_id,
        confirmation_word=payload.confirmation_word,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="storage.remediation_execute",
        resource_type="storage_scan_finding",
        after_json={
            "action": result.action,
            "count": result.count,
            "file_ids": result.file_ids,
            "transfer_uid": result.transfer_uid,
        },
        request=request,
    )
    db.commit()
    return ok(result.model_dump(mode="json"), request.state.request_id)


@router.get("/scans/{scan_id}")
def get_scan(
    scan_id: int,
    request: Request,
    db: DbSession,
    _current_user=Depends(data_reader),
):
    row = db.get(StorageScanRun, scan_id)
    if row is None:
        raise not_found("StorageScanRun")
    return ok(_scan_data(row), request.state.request_id)


@router.get("/scans/{scan_id}/findings")
def list_scan_findings(
    scan_id: int,
    request: Request,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    finding_type: str | None = Query(default=None),
    resolution_status: str | None = Query(default=None),
    _current_user=Depends(data_reader),
):
    if db.get(StorageScanRun, scan_id) is None:
        raise not_found("StorageScanRun")
    statement = select(StorageScanFinding).where(StorageScanFinding.run_id == scan_id)
    if finding_type:
        statement = statement.where(StorageScanFinding.finding_type == finding_type)
    if resolution_status:
        statement = statement.where(
            StorageScanFinding.resolution_status == resolution_status
        )
    rows, total = paginate_scalars(
        db,
        statement.order_by(StorageScanFinding.id),
        page_no=page,
        page_size=page_size,
    )
    data = [
        {
            "id": row.id,
            "finding_type": row.finding_type,
            "bucket": row.bucket,
            "storage_key": row.storage_key,
            "file_id": row.file_id,
            "file_status": row.file_status,
            "database_size_bytes": row.database_size_bytes,
            "object_size_bytes": row.object_size_bytes,
            "object_modified_at": row.object_modified_at,
            "resolution_status": row.resolution_status,
            "resolution_action": row.resolution_action,
        }
        for row in rows
    ]
    return page_response(
        data,
        page,
        page_size,
        total,
        request.state.request_id,
    )
