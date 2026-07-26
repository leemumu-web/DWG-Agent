from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

import app.modules.files.interface as storage_service
from app.modules.files.interface import StorageScanFinding, StorageScanRun
from app.modules.identity.interface import require_roles
from app.modules.operations.audit.interface import write_audit_log
from app.modules.operations.storage_reconciliation.presentation import (
    scan_finding_data,
    scan_run_data,
)
from app.modules.operations.storage_reconciliation.remediation import (
    execute_remediation,
    preview_remediation,
)
from app.platform.config.constants import ROLE_ADMIN
from app.platform.config.settings import settings
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import DbSession
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found

router = APIRouter()
data_reader = require_roles(ROLE_ADMIN)
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
        after_json={
            "scope_bucket": payload.scope_bucket,
            "backend": row.backend,
        },
        request=request,
    )
    db.commit()
    from app.modules.operations.storage_reconciliation.tasks import (
        scan_storage_consistency_task,
    )

    scan_storage_consistency_task.delay(row.id)
    db.expire_all()
    refreshed = db.get(StorageScanRun, row.id) or row
    return ok(scan_run_data(refreshed), request.state.request_id)


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
        [scan_run_data(row) for row in rows],
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
    current_user=Depends(data_writer),
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
    return ok(scan_run_data(row), request.state.request_id)


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
        statement = statement.where(StorageScanFinding.resolution_status == resolution_status)
    rows, total = paginate_scalars(
        db,
        statement.order_by(StorageScanFinding.id),
        page_no=page,
        page_size=page_size,
    )
    return page_response(
        [scan_finding_data(row) for row in rows],
        page,
        page_size,
        total,
        request.state.request_id,
    )
