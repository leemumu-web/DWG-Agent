from __future__ import annotations

from app.modules.files.interface import FileTransfer, StoredFile
from app.platform.storage.base import ObjectInfo


def transfer_data(row: FileTransfer) -> dict:
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


def storage_object_data(
    item: ObjectInfo,
    registered: dict[str, StoredFile],
) -> dict:
    row = registered.get(item.storage_key)
    return {
        "bucket": item.bucket,
        "storage_key": item.storage_key,
        "size_bytes": item.size_bytes,
        "last_modified": item.last_modified,
        "registered": row is not None,
        "file_id": row.id if row is not None else None,
        "file_status": row.status if row is not None else None,
    }


__all__ = ["storage_object_data", "transfer_data"]
