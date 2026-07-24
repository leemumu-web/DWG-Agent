from __future__ import annotations

import hashlib
import mimetypes
from io import BytesIO
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.modules.files.models import StoredFile
from app.modules.files.storage_transactions import (
    _prepare_storage_transfer,
    _register_pending_storage_object,
    _settle_storage_write_failure,
)
from app.modules.files.validation import (
    MIN_DWG_SIZE_BYTES,
    sanitize_filename,
    validate_dwg_header,
    validate_dxf_structure,
    validate_upload_mime,
    validate_upload_name,
)
from app.platform.config.constants import EXCEL_FILE_EXTENSIONS
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage import factory as storage_factory
from app.platform.storage.base import (
    StorageError,
)


async def save_upload_file(
    db: Session,
    upload: UploadFile,
    uploaded_by: int | None,
    batch_name: str | None = None,
    transfer_uid: str | None = None,
    transfer_direction: str = "inbound",
    transfer_operation: str = "upload",
    request_id: str | None = None,
) -> StoredFile:
    original_name = sanitize_filename(upload.filename or "unnamed.dwg")
    file_ext = validate_upload_name(original_name)
    is_excel = file_ext in EXCEL_FILE_EXTENSIONS
    upload_content_type = validate_upload_mime(upload.content_type)
    bucket = (
        settings.minio_bucket_reports
        if is_excel
        else (
            settings.minio_bucket_original
            if file_ext == ".dwg"
            else settings.minio_bucket_dxf_original
        )
    )
    storage_key = f"uploads/{uuid4().hex}{file_ext}"
    storage = storage_factory.get_storage_backend()

    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    max_size = settings.max_upload_size_mb * 1024 * 1024

    with SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as tmp:
        dxf_prefix = bytearray()
        dxf_tail = bytearray()
        try:
            first = True
            while chunk := await upload.read(1024 * 1024):
                if first:
                    if file_ext == ".dwg":
                        validate_dwg_header(chunk)
                    first = False
                size += len(chunk)
                if size > max_size:
                    raise AppHTTPException(413, "FILE_TOO_LARGE", "Uploaded file exceeds max size.")
                sha256.update(chunk)
                md5.update(chunk)
                tmp.write(chunk)
                if file_ext == ".dxf":
                    if len(dxf_prefix) < 65536:
                        dxf_prefix.extend(chunk[: 65536 - len(dxf_prefix)])
                    dxf_tail.extend(chunk)
                    if len(dxf_tail) > 65536:
                        del dxf_tail[:-65536]
            if first:
                if file_ext == ".dwg":
                    validate_dwg_header(b"")
            if size == 0:
                raise AppHTTPException(
                    422,
                    "EMPTY_FILE",
                    "Uploaded file is empty — content must be at least 1 byte.",
                )
            if file_ext == ".dxf":
                validate_dxf_structure(bytes(dxf_prefix + dxf_tail))
        except AppHTTPException:
            raise

        if file_ext == ".dwg" and size < MIN_DWG_SIZE_BYTES:
            raise AppHTTPException(
                415,
                "FILE_NOT_DWG",
                f"File too small ({size} bytes) — legitimate DWG files exceed {MIN_DWG_SIZE_BYTES} bytes.",
            )

        content_type = upload_content_type or mimetypes.guess_type(original_name)[0]
        auto_transfer = transfer_uid is None
        durable_intent = transfer_uid is not None
        if auto_transfer:
            request_id = request_id or f"upload:{hashlib.sha256(storage_key.encode()).hexdigest()[:43]}"
            transfer_uid, durable_intent = _prepare_storage_transfer(
                db,
                direction=transfer_direction,
                operation=transfer_operation,
                actor_user_id=uploaded_by,
                request_id=request_id,
                batch_ref=batch_name,
                bucket=bucket,
                storage_key=storage_key,
                original_name=original_name,
                expected_bytes=size,
            )
        elif transfer_uid is not None:
            from app.modules.files.storage_transactions import (
                mark_transfer_in_progress,
                session_factory_for,
            )

            mark_transfer_in_progress(
                session_factory_for(db),
                transfer_uid,
                bucket=bucket,
                storage_key=storage_key,
                expected_bytes=size,
            )
        try:
            storage.put_fileobj(
                bucket,
                storage_key,
                tmp,
                length=size,
                content_type=content_type,
            )
            _register_pending_storage_object(
                db,
                storage,
                bucket,
                storage_key,
                size_bytes=size,
                transfer_uid=transfer_uid,
            )
        except StorageError as exc:
            if transfer_uid is not None:
                _settle_storage_write_failure(
                    db,
                    transfer_uid,
                    durable_intent=durable_intent,
                )
            raise AppHTTPException(
                503,
                "STORAGE_WRITE_FAILED",
                "Failed to persist uploaded file.",
            ) from exc

    stored = StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=file_ext,
        content_type=content_type,
        size_bytes=size,
        sha256=sha256.hexdigest(),
        md5=md5.hexdigest(),
        batch_name=batch_name,
        uploaded_by=uploaded_by,
        status="available",
    )
    db.add(stored)
    db.flush()
    if auto_transfer and transfer_uid is not None:
        from app.modules.files.storage_transactions import complete_transfer_in_transaction

        complete_transfer_in_transaction(
            db,
            transfer_uid,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            transferred_bytes=stored.size_bytes,
        )
    return stored

def save_bytes_as_file(
    db: Session,
    *,
    bucket: str,
    storage_key: str,
    original_name: str,
    file_ext: str,
    content_type: str,
    payload: bytes,
    uploaded_by: int | None,
    batch_name: str | None = None,
    transfer_uid: str | None = None,
    transfer_direction: str = "internal",
    transfer_operation: str = "generated",
    request_id: str | None = None,
) -> StoredFile:
    storage = storage_factory.get_storage_backend()
    auto_transfer = transfer_uid is None
    # An explicit transfer is prepared and committed by the caller before the
    # generated-file metadata transaction.  MySQL failures therefore need an
    # independent settlement; SQLite tests keep the intent in this transaction.
    durable_intent = (
        transfer_uid is not None and db.get_bind().dialect.name != "sqlite"
    )
    if auto_transfer:
        request_id = request_id or f"generated:{hashlib.sha256(storage_key.encode()).hexdigest()[:40]}"
        transfer_uid, durable_intent = _prepare_storage_transfer(
            db,
            direction=transfer_direction,
            operation=transfer_operation,
            actor_user_id=uploaded_by,
            request_id=request_id,
            batch_ref=batch_name,
            bucket=bucket,
            storage_key=storage_key,
            original_name=original_name,
            expected_bytes=len(payload),
        )
    try:
        storage.put_fileobj(
            bucket,
            storage_key,
            BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )
        _register_pending_storage_object(
            db,
            storage,
            bucket,
            storage_key,
            size_bytes=len(payload),
            transfer_uid=transfer_uid,
        )
    except StorageError as exc:
        if transfer_uid is not None:
            _settle_storage_write_failure(
                db,
                transfer_uid,
                durable_intent=durable_intent,
            )
        raise AppHTTPException(
            503,
            "STORAGE_WRITE_FAILED",
            "Failed to persist generated file.",
        ) from exc

    stored = StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=file_ext,
        content_type=content_type,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        md5=hashlib.md5(payload).hexdigest(),
        batch_name=batch_name,
        uploaded_by=uploaded_by,
        status="available",
    )
    db.add(stored)
    db.flush()
    if auto_transfer and transfer_uid is not None:
        from app.modules.files.storage_transactions import complete_transfer_in_transaction

        complete_transfer_in_transaction(
            db,
            transfer_uid,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            transferred_bytes=stored.size_bytes,
        )
    return stored

def save_path_as_file(
    db: Session,
    *,
    bucket: str,
    storage_key: str,
    original_name: str,
    file_ext: str,
    content_type: str,
    source_path: Path,
    uploaded_by: int | None,
    batch_name: str | None = None,
    transfer_uid: str | None = None,
    transfer_direction: str = "internal",
    transfer_operation: str = "generated",
    request_id: str | None = None,
) -> StoredFile:
    """Persist a generated file without reading the complete payload into memory."""
    if not source_path.is_file():
        raise AppHTTPException(
            422,
            "GENERATED_FILE_MISSING",
            "Generated file is not available for persistence.",
        )
    size = source_path.stat().st_size
    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    with source_path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            sha256.update(chunk)
            md5.update(chunk)

    storage = storage_factory.get_storage_backend()
    auto_transfer = transfer_uid is None
    durable_intent = (
        transfer_uid is not None and db.get_bind().dialect.name != "sqlite"
    )
    if auto_transfer:
        request_id = request_id or f"generated:{hashlib.sha256(storage_key.encode()).hexdigest()[:40]}"
        transfer_uid, durable_intent = _prepare_storage_transfer(
            db,
            direction=transfer_direction,
            operation=transfer_operation,
            actor_user_id=uploaded_by,
            request_id=request_id,
            batch_ref=batch_name,
            bucket=bucket,
            storage_key=storage_key,
            original_name=original_name,
            expected_bytes=size,
        )
    try:
        with source_path.open("rb") as source:
            storage.put_fileobj(
                bucket,
                storage_key,
                source,
                length=size,
                content_type=content_type,
            )
        _register_pending_storage_object(
            db,
            storage,
            bucket,
            storage_key,
            size_bytes=size,
            transfer_uid=transfer_uid,
        )
    except StorageError as exc:
        if transfer_uid is not None:
            _settle_storage_write_failure(
                db,
                transfer_uid,
                durable_intent=durable_intent,
            )
        raise AppHTTPException(
            503,
            "STORAGE_WRITE_FAILED",
            "Failed to persist generated file.",
        ) from exc

    stored = StoredFile(
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        file_ext=file_ext,
        content_type=content_type,
        size_bytes=size,
        sha256=sha256.hexdigest(),
        md5=md5.hexdigest(),
        batch_name=batch_name,
        uploaded_by=uploaded_by,
        status="available",
    )
    db.add(stored)
    db.flush()
    if auto_transfer and transfer_uid is not None:
        from app.modules.files.storage_transactions import complete_transfer_in_transaction

        complete_transfer_in_transaction(
            db,
            transfer_uid,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            transferred_bytes=stored.size_bytes,
        )
    return stored

def get_local_file_path(file: StoredFile) -> Path:
    return storage_factory.build_storage_path(file.bucket, file.storage_key)
