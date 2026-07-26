from __future__ import annotations

from datetime import UTC, datetime, time

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

import app.modules.files.interface as storage_service
from app.modules.files.interface import (
    FileTransfer,
    StorageScanRun,
    StoredFile,
)
from app.platform.config.settings import settings
from app.platform.database.pagination import paginate_scalars
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import StorageConfigurationError, StorageError


def data_overview(db: Session, *, owner_user_id: int | None = None) -> dict:
    file_count_statement = select(
        StoredFile.status, func.count(StoredFile.id)
    ).group_by(StoredFile.status)
    tracked_bytes_statement = select(
        func.coalesce(func.sum(StoredFile.size_bytes), 0)
    ).where(StoredFile.status == "available")
    latest_scan_statement = select(StorageScanRun).order_by(StorageScanRun.id.desc()).limit(1)
    if owner_user_id is not None:
        file_count_statement = file_count_statement.where(
            StoredFile.uploaded_by == owner_user_id
        )
        tracked_bytes_statement = tracked_bytes_statement.where(
            StoredFile.uploaded_by == owner_user_id
        )
        latest_scan_statement = latest_scan_statement.where(
            StorageScanRun.actor_user_id == owner_user_id
        )
    counts = dict(db.execute(file_count_statement).all())
    tracked_bytes = db.scalar(tracked_bytes_statement)
    latest_scan = db.scalar(latest_scan_statement)
    today_start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    transfer_counts = {
        (direction, status): int(count)
        for direction, status, count in db.execute(
            select(
                FileTransfer.direction,
                FileTransfer.status,
                func.count(FileTransfer.id),
            )
            .where(
                FileTransfer.created_at >= today_start,
                *(
                    (FileTransfer.actor_user_id == owner_user_id,)
                    if owner_user_id is not None
                    else ()
                ),
            )
            .group_by(FileTransfer.direction, FileTransfer.status)
        ).all()
    }
    storage_status = "ok"
    storage = None
    try:
        storage = storage_service.get_storage_backend()
        storage.check_health()
    except (AppHTTPException, StorageConfigurationError, StorageError):
        storage_status = "error"
    capacity = storage.capacity() if storage is not None else None
    area_purposes = (
        (settings.minio_bucket_original, "source_dwg"),
        (settings.minio_bucket_derived, "derived_dwg"),
        (settings.minio_bucket_reports, "reports"),
        (settings.minio_bucket_temp, "temporary"),
        (settings.minio_bucket_dxf_original, "source_dxf"),
        (settings.minio_bucket_dxf_derived, "derived_dxf"),
    )
    configured_areas: dict[str, list[str]] = {}
    for bucket, purpose in area_purposes:
        configured_areas.setdefault(bucket, []).append(purpose)

    return {
        "status": "ok" if storage_status == "ok" else "degraded",
        "environment": {
            "app_env": settings.app_env,
            "database_engine": db.get_bind().dialect.name,
            "database": settings.mysql_database,
            "storage_backend": settings.storage_backend,
        },
        "database": {"status": "ok"},
        "storage": {
            "status": storage_status,
            "areas": [
                {"bucket": bucket, "purpose_codes": purpose_codes}
                for bucket, purpose_codes in configured_areas.items()
            ],
            "capacity": {
                "status": capacity.status if capacity else "unknown",
                "total_bytes": capacity.total_bytes if capacity else None,
                "used_bytes": capacity.used_bytes if capacity else None,
                "free_bytes": capacity.free_bytes if capacity else None,
                "used_percent": capacity.used_percent if capacity else None,
                "reason": capacity.reason if capacity else "capacity_backend_unavailable",
                "checked_at": capacity.checked_at.isoformat() if capacity else None,
            },
        },
        "catalog": {
            "available_files": int(counts.get("available", 0)),
            "deleted_files": int(counts.get("deleted", 0)),
            "tracked_bytes": int(tracked_bytes or 0),
        },
        "transfers_today": {
            "inbound_succeeded": transfer_counts.get(
                ("inbound", "succeeded"),
                0,
            ),
            "outbound_succeeded": transfer_counts.get(
                ("outbound", "succeeded"),
                0,
            ),
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


def query_data_files(
    db: Session,
    *,
    page: int,
    page_size: int,
    search: str,
    status: str | None,
    bucket: str | None,
    file_ext: str | None,
    owner_user_id: int | None = None,
) -> tuple[list[StoredFile], int]:
    statement = select(StoredFile)
    if owner_user_id is not None:
        statement = statement.where(StoredFile.uploaded_by == owner_user_id)
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
    return paginate_scalars(
        db,
        statement.order_by(StoredFile.id.desc()),
        page_no=page,
        page_size=page_size,
    )


def registered_files_by_storage_key(
    db: Session,
    *,
    bucket: str,
    keys: list[str],
) -> dict[str, StoredFile]:
    if not keys:
        return {}
    return {
        row.storage_key: row
        for row in db.scalars(
            select(StoredFile).where(
                StoredFile.bucket == bucket,
                StoredFile.storage_key.in_(keys),
            )
        ).all()
    }


def query_transfers(
    db: Session,
    *,
    page: int,
    page_size: int,
    direction: str | None,
    status: str | None,
    operation: str | None,
    file_id: int | None,
    actor_user_id: int | None = None,
) -> tuple[list[FileTransfer], int]:
    statement = select(FileTransfer)
    if actor_user_id is not None:
        statement = statement.where(FileTransfer.actor_user_id == actor_user_id)
    if direction:
        statement = statement.where(FileTransfer.direction == direction)
    if status:
        statement = statement.where(FileTransfer.status == status)
    if operation:
        statement = statement.where(FileTransfer.operation == operation)
    if file_id is not None:
        statement = statement.where(FileTransfer.file_id == file_id)
    return paginate_scalars(
        db,
        statement.order_by(FileTransfer.id.desc()),
        page_no=page,
        page_size=page_size,
    )


__all__ = [
    "data_overview",
    "query_data_files",
    "query_transfers",
    "registered_files_by_storage_key",
]
