"""Admin-only, registry-preserving MinIO object mutations."""

from __future__ import annotations

from pathlib import PurePosixPath
from tempfile import SpooledTemporaryFile

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select

import app.modules.files.interface as file_service
from app.modules.files.interface import (
    FileRead,
    StoredFile,
    TransferSpec,
    begin_transfer,
    mark_transfer_in_progress,
    session_factory_for,
    settle_transfer,
)
from app.modules.identity.interface import require_roles
from app.modules.operations.audit.interface import write_audit_log
from app.platform.config.constants import ROLE_ADMIN
from app.platform.config.settings import settings
from app.platform.http.dependencies import DbSession
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException, not_found
from app.platform.storage.base import StorageError

router = APIRouter()
data_writer = require_roles(ROLE_ADMIN)


class ObjectMoveRequest(BaseModel):
    bucket: str
    storage_key: str
    target_bucket: str
    target_storage_key: str


def _validate_location(bucket: str, storage_key: str) -> tuple[str, str]:
    if bucket not in settings.minio_bucket_names:
        raise AppHTTPException(422, "INVALID_BUCKET", "Bucket is not configured.")
    key = storage_key.strip().lstrip("/")
    path = PurePosixPath(key)
    if (
        not key
        or key.endswith("/")
        or "\\" in key
        or ".." in path.parts
        or len(key) > 512
    ):
        raise AppHTTPException(422, "INVALID_STORAGE_KEY", "Object key is invalid.")
    return bucket, key


@router.delete("/objects", status_code=status.HTTP_204_NO_CONTENT)
def delete_registered_object(
    request: Request,
    db: DbSession,
    bucket: str = Query(...),
    storage_key: str = Query(..., max_length=512),
    current_user=Depends(data_writer),
):
    bucket, storage_key = _validate_location(bucket, storage_key)
    stored = db.scalar(
        select(StoredFile).where(
            StoredFile.bucket == bucket,
            StoredFile.storage_key == storage_key,
            StoredFile.status != "deleted",
        )
    )
    if stored is None:
        raise AppHTTPException(
            409,
            "REGISTERED_OBJECT_REQUIRED",
            "Only registered, active files can be deleted from the data console.",
        )
    file_service.require_file_delete_access(db, current_user, stored)
    file_service.soft_delete_file_in_transaction(
        db,
        stored,
        actor_user_id=current_user.id,
        request_id=request.state.request_id,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="data_console.object_soft_delete",
        resource_type="file",
        resource_id=stored.id,
        before_json={"bucket": bucket, "storage_key": storage_key},
        request=request,
    )
    db.commit()
    return None


@router.post("/objects/moves")
def move_registered_object(
    payload: ObjectMoveRequest,
    request: Request,
    db: DbSession,
    current_user=Depends(data_writer),
):
    source_bucket, source_key = _validate_location(payload.bucket, payload.storage_key)
    target_bucket, target_key = _validate_location(
        payload.target_bucket,
        payload.target_storage_key,
    )
    if (source_bucket, source_key) == (target_bucket, target_key):
        raise AppHTTPException(422, "OBJECT_LOCATION_UNCHANGED", "Target equals source.")

    stored = db.scalar(
        select(StoredFile).where(
            StoredFile.bucket == source_bucket,
            StoredFile.storage_key == source_key,
            StoredFile.status != "deleted",
        )
    )
    if stored is None:
        raise not_found("RegisteredStorageObject")
    if db.scalar(
        select(StoredFile.id).where(
            StoredFile.bucket == target_bucket,
            StoredFile.storage_key == target_key,
        )
    ) is not None:
        raise AppHTTPException(409, "OBJECT_TARGET_EXISTS", "Target key is already registered.")

    file_id = stored.id
    original_name = stored.original_name
    expected_bytes = stored.size_bytes
    factory = session_factory_for(db)
    db.rollback()
    storage = file_service.get_storage_backend()
    try:
        if storage.object_exists(target_bucket, target_key):
            raise AppHTTPException(409, "OBJECT_TARGET_EXISTS", "Target object already exists.")
    except StorageError as exc:
        raise AppHTTPException(503, "STORAGE_STAT_FAILED", "Target could not be checked.") from exc

    transfer = begin_transfer(
        factory,
        TransferSpec(
            direction="internal",
            operation="move",
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
            file_id=file_id,
            bucket=source_bucket,
            storage_key=source_key,
            original_name=original_name,
            expected_bytes=expected_bytes,
        ),
    )
    target_written = False
    try:
        mark_transfer_in_progress(
            factory,
            transfer.transfer_uid,
            bucket=target_bucket,
            storage_key=target_key,
            expected_bytes=expected_bytes,
        )
        copied = 0
        with SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as spool:
            for chunk in storage.iter_file(source_bucket, source_key):
                spool.write(chunk)
                copied += len(chunk)
            if copied != expected_bytes:
                raise AppHTTPException(
                    409,
                    "OBJECT_SOURCE_CHANGED",
                    "Source size changed during the move.",
                )
            storage.put_fileobj(
                target_bucket,
                target_key,
                spool,
                length=copied,
                content_type=stored.content_type,
            )
            target_written = True
        target_stat = storage.stat_object(target_bucket, target_key)
        if target_stat.size_bytes != expected_bytes:
            raise AppHTTPException(503, "OBJECT_COPY_MISMATCH", "Copied object size mismatched.")

        with factory.begin() as mutation_db:
            locked = mutation_db.scalar(
                select(StoredFile)
                .where(StoredFile.id == file_id)
                .with_for_update()
            )
            if (
                locked is None
                or locked.status == "deleted"
                or locked.bucket != source_bucket
                or locked.storage_key != source_key
            ):
                raise AppHTTPException(
                    409,
                    "OBJECT_SOURCE_CHANGED",
                    "Registered source changed during the move.",
                )
            locked.bucket = target_bucket
            locked.storage_key = target_key
            write_audit_log(
                mutation_db,
                actor_user_id=current_user.id,
                action="data_console.object_move",
                resource_type="file",
                resource_id=file_id,
                before_json={"bucket": source_bucket, "storage_key": source_key},
                after_json={"bucket": target_bucket, "storage_key": target_key},
                request=request,
            )

        try:
            storage.delete_object(source_bucket, source_key)
        except StorageError as exc:
            settle_transfer(
                factory,
                transfer.transfer_uid,
                status="compensation_required",
                transferred_bytes=copied,
                error_code="OBJECT_SOURCE_DELETE_FAILED",
                error_message="Target is authoritative but the source copy remains.",
            )
            raise AppHTTPException(
                503,
                "OBJECT_MOVE_COMPENSATION_REQUIRED",
                "Object was moved in the registry but the source copy could not be removed.",
            ) from exc
        settle_transfer(
            factory,
            transfer.transfer_uid,
            status="succeeded",
            transferred_bytes=copied,
        )
    except Exception as exc:
        if target_written:
            try:
                current = db.get(StoredFile, file_id)
                target_is_authoritative = bool(
                    current
                    and current.bucket == target_bucket
                    and current.storage_key == target_key
                )
                if not target_is_authoritative:
                    storage.delete_object(target_bucket, target_key)
            except Exception:
                pass
        if not (
            isinstance(exc, AppHTTPException)
            and exc.detail.get("code") == "OBJECT_MOVE_COMPENSATION_REQUIRED"
        ):
            settle_transfer(
                factory,
                transfer.transfer_uid,
                status="failed",
                transferred_bytes=0,
                error_code="OBJECT_MOVE_FAILED",
                error_message="Registered object move failed.",
            )
        raise

    db.expire_all()
    moved = db.get(StoredFile, file_id)
    assert moved is not None
    return ok(FileRead.model_validate(moved), request.state.request_id)


__all__ = ["router"]
