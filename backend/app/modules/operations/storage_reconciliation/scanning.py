from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.files.interface import (
    StorageScanFinding,
    StorageScanRun,
    StoredFile,
)
from app.platform.database.mixins import utcnow
from app.platform.storage.base import AbstractStorageBackend, ObjectInfo


def _load_file_snapshot(
    factory: sessionmaker[Session],
    buckets: list[str],
) -> dict[tuple[str, str], tuple[int, str, int]]:
    with factory() as db:
        rows = db.execute(
            select(
                StoredFile.id,
                StoredFile.bucket,
                StoredFile.storage_key,
                StoredFile.status,
                StoredFile.size_bytes,
            ).where(StoredFile.bucket.in_(buckets))
        ).all()
    return {(row.bucket, row.storage_key): (row.id, row.status, row.size_bytes) for row in rows}


def _load_object_snapshot(
    storage: AbstractStorageBackend,
    buckets: list[str],
) -> dict[tuple[str, str], ObjectInfo]:
    objects: dict[tuple[str, str], ObjectInfo] = {}
    for bucket in buckets:
        cursor: str | None = None
        while True:
            page = storage.list_objects(
                bucket,
                prefix="",
                cursor=cursor,
                page_size=200,
            )
            for item in page.items:
                objects[(bucket, item.storage_key)] = item
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
    return objects


def execute_scan_run(
    scan_run_id: int,
    *,
    factory: sessionmaker[Session],
    storage: AbstractStorageBackend,
    buckets: list[str],
) -> None:
    scoped_buckets = buckets
    with factory.begin() as db:
        run = db.get(StorageScanRun, scan_run_id)
        if run is None or run.status in {"succeeded", "failed", "cancelled"}:
            return
        if run.scope_bucket is not None:
            scoped_buckets = [run.scope_bucket]
        run.status = "running"
        run.started_at = run.started_at or utcnow()
        run.error_code = None
        run.error_message = None

    try:
        files = _load_file_snapshot(factory, scoped_buckets)
        objects = _load_object_snapshot(storage, scoped_buckets)
        findings: list[StorageScanFinding] = []
        consistent_count = 0
        retained_deleted_count = 0
        missing_object_count = 0
        size_mismatch_count = 0

        for location, (file_id, file_status, database_size) in files.items():
            bucket, storage_key = location
            object_info = objects.get(location)
            if file_status == "deleted":
                if object_info is not None:
                    retained_deleted_count += 1
                    findings.append(
                        StorageScanFinding(
                            run_id=scan_run_id,
                            finding_type="retained_deleted",
                            bucket=bucket,
                            storage_key=storage_key,
                            file_id=file_id,
                            file_status=file_status,
                            database_size_bytes=database_size,
                            object_size_bytes=object_info.size_bytes,
                            object_modified_at=object_info.last_modified,
                        )
                    )
                continue
            if file_status != "available":
                continue
            if object_info is None:
                missing_object_count += 1
                findings.append(
                    StorageScanFinding(
                        run_id=scan_run_id,
                        finding_type="missing_object",
                        bucket=bucket,
                        storage_key=storage_key,
                        file_id=file_id,
                        file_status=file_status,
                        database_size_bytes=database_size,
                    )
                )
            elif object_info.size_bytes != database_size:
                size_mismatch_count += 1
                findings.append(
                    StorageScanFinding(
                        run_id=scan_run_id,
                        finding_type="size_mismatch",
                        bucket=bucket,
                        storage_key=storage_key,
                        file_id=file_id,
                        file_status=file_status,
                        database_size_bytes=database_size,
                        object_size_bytes=object_info.size_bytes,
                        object_modified_at=object_info.last_modified,
                    )
                )
            else:
                consistent_count += 1

        untracked_object_count = 0
        for location, object_info in objects.items():
            if location in files:
                continue
            untracked_object_count += 1
            findings.append(
                StorageScanFinding(
                    run_id=scan_run_id,
                    finding_type="untracked_object",
                    bucket=object_info.bucket,
                    storage_key=object_info.storage_key,
                    object_size_bytes=object_info.size_bytes,
                    object_modified_at=object_info.last_modified,
                )
            )

        with factory.begin() as db:
            run = db.get(StorageScanRun, scan_run_id)
            if run is None or run.status == "cancelled":
                return
            db.execute(delete(StorageScanFinding).where(StorageScanFinding.run_id == scan_run_id))
            db.add_all(findings)
            run.scanned_files = len(files)
            run.scanned_objects = len(objects)
            run.consistent_count = consistent_count
            run.retained_deleted_count = retained_deleted_count
            run.missing_object_count = missing_object_count
            run.untracked_object_count = untracked_object_count
            run.size_mismatch_count = size_mismatch_count
            run.error_count = 0
            run.status = "succeeded"
            run.finished_at = utcnow()
    except Exception:
        with factory.begin() as db:
            run = db.get(StorageScanRun, scan_run_id)
            if run is not None and run.status != "cancelled":
                run.status = "failed"
                run.error_count = 1
                run.error_code = "STORAGE_SCAN_FAILED"
                run.error_message = "Storage consistency scan could not be completed."
                run.finished_at = utcnow()
        raise


__all__ = ["execute_scan_run"]
