from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.modules.identity.interface import require_roles
from app.modules.operations.audit.interface import write_audit_log
from app.modules.operations.daily_archive.models import DailyArchiveRun
from app.modules.operations.daily_archive.planning import (
    prepare_daily_archive_run,
    preview_daily_archive,
)
from app.modules.operations.daily_archive.presentation import daily_archive_run_data
from app.platform.config.constants import ROLE_ADMIN, ROLE_AUDITOR
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import DbSession
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found

router = APIRouter()
data_reader = require_roles(ROLE_ADMIN, ROLE_AUDITOR)
data_writer = require_roles(ROLE_ADMIN)


class DailyArchivePreviewRequest(BaseModel):
    archive_date: date | None = None
    scope_bucket: str | None = None


class DailyArchiveCreateRequest(BaseModel):
    preview_token: str
    idempotency_key: str


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
        from app.modules.operations.daily_archive.tasks import (
            create_daily_archive_task,
        )

        try:
            result = create_daily_archive_task.apply_async(
                args=[run_id],
                queue="maintenance",
            )
        except Exception as exc:
            with db.begin():
                failed = db.get(
                    DailyArchiveRun,
                    run_id,
                    populate_existing=True,
                )
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
            refreshed = db.get(
                DailyArchiveRun,
                run_id,
                populate_existing=True,
            )
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
