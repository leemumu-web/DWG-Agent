from __future__ import annotations

from app.modules.files.interface import StorageScanFinding, StorageScanRun


def scan_run_data(row: StorageScanRun) -> dict:
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


def scan_finding_data(row: StorageScanFinding) -> dict:
    return {
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


__all__ = ["scan_finding_data", "scan_run_data"]
