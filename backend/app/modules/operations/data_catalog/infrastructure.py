from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, get_storage_backend
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import StorageConfigurationError, StorageError


def _check_database(db: Session) -> dict:
    started = monotonic()
    try:
        db.execute(text("SELECT 1"))
        table_count = (
            db.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE()"
                )
            )
            if db.bind and db.bind.dialect.name == "mysql"
            else len(inspect(db.bind).get_table_names())
            if db.bind
            else None
        )
        return {
            "status": "ok",
            "engine": db.bind.dialect.name if db.bind else "unknown",
            "database": settings.mysql_database,
            "latency_ms": round((monotonic() - started) * 1000, 1),
            "table_count": table_count,
            "pool": {
                "size": settings.db_pool_size,
                "max_overflow": settings.db_pool_max_overflow,
                "recycle_seconds": settings.db_pool_recycle_seconds,
            },
        }
    except Exception:
        return {
            "status": "error",
            "engine": db.bind.dialect.name if db.bind else "unknown",
            "database": settings.mysql_database,
            "latency_ms": round((monotonic() - started) * 1000, 1),
            "table_count": None,
            "pool": {
                "size": settings.db_pool_size,
                "max_overflow": settings.db_pool_max_overflow,
                "recycle_seconds": settings.db_pool_recycle_seconds,
            },
        }


def _check_storage(db: Session) -> dict:
    started = monotonic()
    bucket_names = settings.minio_bucket_names
    bucket_objects: dict[str, int | None] = {name: None for name in bucket_names}
    try:
        backend = get_storage_backend()
    except (AppHTTPException, StorageConfigurationError):
        return {
            "status": "error",
            "backend": settings.storage_backend,
            "latency_ms": round((monotonic() - started) * 1000, 1),
            "buckets": [],
        }
    try:
        backend.check_health()
        bucket_objects = backend.bucket_object_counts(bucket_names)
        tracked = dict(
            db.execute(
                select(StoredFile.bucket, func.count(StoredFile.id))
                .where(StoredFile.status == "available")
                .group_by(StoredFile.bucket)
            ).all()
        )
        return {
            "status": "ok",
            "backend": settings.storage_backend,
            "latency_ms": round((monotonic() - started) * 1000, 1),
            "buckets": [
                {
                    "name": name,
                    "tracked_files": int(tracked.get(name, 0)),
                    "object_count": bucket_objects[name],
                }
                for name in bucket_names
            ],
        }
    except StorageError:
        return {
            "status": "error",
            "backend": settings.storage_backend,
            "latency_ms": round((monotonic() - started) * 1000, 1),
            "buckets": [],
        }


def infrastructure_overview(db: Session) -> dict:
    database = _check_database(db)
    storage = _check_storage(db)
    available_files, tracked_bytes = db.execute(
        select(func.count(StoredFile.id), func.coalesce(func.sum(StoredFile.size_bytes), 0)).where(
            StoredFile.status == "available"
        )
    ).one()
    extension_counts = dict(
        db.execute(
            select(StoredFile.file_ext, func.count(StoredFile.id))
            .where(StoredFile.status == "available")
            .group_by(StoredFile.file_ext)
            .order_by(func.count(StoredFile.id).desc())
            .limit(8)
        ).all()
    )
    # Disk capacity: available for local storage backend; unknown for remote.
    capacity: dict = {"status": "unknown", "disk_total_bytes": None, "disk_free_bytes": None}
    if settings.storage_backend == "local":
        try:
            root = Path(settings.local_storage_root)
            root.mkdir(parents=True, exist_ok=True)
            usage = shutil.disk_usage(root)
            capacity = {
                "status": "ok",
                "disk_total_bytes": usage.total,
                "disk_used_bytes": usage.used,
                "disk_free_bytes": usage.free,
            }
        except OSError:
            capacity["status"] = "error"

    return {
        "status": "ok" if database["status"] == storage["status"] == "ok" else "degraded",
        "checked_at": datetime.now(UTC).isoformat(),
        "database": database,
        "storage": storage,
        "catalog": {
            "available_files": int(available_files),
            "tracked_bytes": int(tracked_bytes),
            "extensions": extension_counts,
        },
        "capacity": capacity,
        "recovery": {
            "consistency_rule": "MySQL metadata and object storage must be backed up and restored as one recovery set.",
            "automated_backup": False,
        },
    }
