from __future__ import annotations

import hashlib
import logging
import mimetypes
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import ALLOWED_UPLOAD_EXTENSIONS
from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile
from app.storage.base import AbstractStorageBackend, StorageConfigurationError, StorageError
from app.storage.local_storage import LocalFileStorage
from app.storage.minio_storage import MinioStorage
from app.utils.path_utils import ensure_within_root

ALLOWED_DWG_MIME_TYPES = {
    "application/acad",
    "application/autocad",
    "application/dwg",
    "application/octet-stream",
    "application/x-acad",
    "application/x-autocad",
    "application/x-dwg",
    "image/vnd.dwg",
    # Common browser fallbacks for .dwg (varies by OS / MIME database):
    "application/vnd.dwg",
    "application/x-extension-dwg",
    "drawing/x-dwg",
    "image/x-dwg",
    "model/vnd.dwg",
}

# MIME types that browsers send for unrecognised binary files — allow these
# through because the DWG header check (§10.3 ¶4) is the real security boundary.
_BINARY_FALLBACK_MIME = {
    "",
    "application/binary",
    "application/download",
    "application/x-binary",
    "application/x-msdownload",
    "binary/octet-stream",
}

logger = logging.getLogger(__name__)
_PENDING_STORAGE_OBJECTS = "pending_storage_objects"
_PENDING_DESTRUCTIVE_TRANSFERS = "pending_destructive_transfers"

SUPPORTED_DWG_HEADERS = {
    b"AC1012",  # AutoCAD R13
    b"AC1014",  # AutoCAD R14
    b"AC1015",  # AutoCAD 2000/2000i/2002
    b"AC1018",  # AutoCAD 2004/2005/2006
    b"AC1021",  # AutoCAD 2007/2008/2009
    b"AC1024",  # AutoCAD 2010/2011/2012
    b"AC1027",  # AutoCAD 2013/2014/2015/2016/2017
    b"AC1032",  # AutoCAD 2018+
}


def _prepare_storage_transfer(
    db: Session,
    *,
    direction: str,
    operation: str,
    actor_user_id: int | None,
    request_id: str,
    batch_ref: str | None,
    bucket: str,
    storage_key: str,
    original_name: str,
    expected_bytes: int,
) -> tuple[str, bool]:
    """Create an in-progress transfer intent before an object write.

    MySQL uses an independent committed intent so a later metadata rollback can
    still settle compensation. SQLite's in-memory StaticPool cannot host an
    independent concurrent transaction, so tests prepare the row in the caller
    transaction while preserving the same state machine fields.
    """
    from app.models.file_transfer import FileTransfer
    from app.models.mixins import utcnow
    from app.services.file_transfer_service import (
        TransferSpec,
        begin_transfer,
        mark_transfer_in_progress,
        prepare_transfer_in_transaction,
        session_factory_for,
    )

    spec = TransferSpec(
        direction=direction,
        operation=operation,
        actor_user_id=actor_user_id,
        request_id=request_id,
        batch_ref=batch_ref,
        bucket=bucket,
        storage_key=storage_key,
        original_name=original_name,
        expected_bytes=expected_bytes,
    )
    if db.get_bind().dialect.name == "sqlite":
        snapshot = prepare_transfer_in_transaction(db, spec)
        row = db.scalar(
            select(FileTransfer).where(FileTransfer.transfer_uid == snapshot.transfer_uid)
        )
        assert row is not None
        row.status = "in_progress"
        row.started_at = row.started_at or utcnow()
        return row.transfer_uid, False

    factory = session_factory_for(db)
    snapshot = begin_transfer(factory, spec)
    mark_transfer_in_progress(
        factory,
        snapshot.transfer_uid,
        bucket=bucket,
        storage_key=storage_key,
        expected_bytes=expected_bytes,
    )
    return snapshot.transfer_uid, True


def _settle_storage_write_failure(
    db: Session,
    transfer_uid: str,
    *,
    durable_intent: bool,
) -> None:
    from app.models.file_transfer import FileTransfer
    from app.models.mixins import utcnow
    from app.services.file_transfer_service import session_factory_for, settle_transfer

    if durable_intent:
        settle_transfer(
            session_factory_for(db),
            transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code="STORAGE_WRITE_FAILED",
            error_message="Object storage rejected the write before metadata commit.",
        )
        return
    row = db.scalar(
        select(FileTransfer).where(FileTransfer.transfer_uid == transfer_uid)
    )
    if row is not None:
        row.status = "failed"
        row.error_code = "STORAGE_WRITE_FAILED"
        row.error_message = "Object storage rejected the write before metadata commit."
        row.started_at = row.started_at or utcnow()
        row.finished_at = utcnow()


def sanitize_filename(name: str) -> str:
    """Strip path traversal, control chars, and injection vectors from a filename.

    Keeps: alphanumerics, CJK characters, dots, dashes, underscores, spaces,
    and common safe punctuation.  Everything else is replaced with '_'.
    Leading dots and dashes are stripped (prevents hidden files on Unix).
    The result is never empty — falls back to 'unnamed'.

    Call this on every user-supplied filename before storing, serving, or
    embedding in headers / archive paths.
    """
    import unicodedata

    # 1. Normalise Unicode (NFKC) to collapse look-alike characters
    name = unicodedata.normalize("NFKC", name)

    # 2. Strip directory separators and null bytes
    for ch in ("\x00", "/", "\\"):
        name = name.replace(ch, "_")

    # 3. Collapse ".." to prevent traversal
    while ".." in name:
        name = name.replace("..", ".")

    # 4. Keep only safe characters
    safe: list[str] = []
    for ch in name:
        cp = ord(ch)
        if (
            ch in "._- @()+,"  # common safe punctuation
            or ch.isalnum()  # A-Z a-z 0-9 + Unicode alnum (covers CJK)
            or cp > 127  # non-ASCII (CJK, Cyrillic, etc.)
        ) and cp >= 32:  # no control chars
            safe.append(ch)
        else:
            safe.append("_")
    clean = "".join(safe)

    # 5. Strip leading dots/dashes (hidden files) and trailing dots/spaces (Windows)
    clean = clean.lstrip(".-").rstrip(". ")

    # 6. Fallback
    if not clean:
        clean = "unnamed"

    # 7. Truncate to reasonable max
    if len(clean) > 200:
        ext = clean.rsplit(".", 1)[-1] if "." in clean else ""
        base = clean.rsplit(".", 1)[0] if "." in clean else clean
        clean = base[: 200 - len(ext) - 1] + "." + ext if ext else base[:200]

    return clean


def validate_upload_name(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise AppHTTPException(
            415,
            "FILE_TYPE_NOT_ALLOWED",
            "File type not allowed for upload.",
            {"allowed_extensions": sorted(ALLOWED_UPLOAD_EXTENSIONS)},
        )
    return ext


def validate_upload_mime(content_type: str | None) -> str | None:
    """Validate the upload Content-Type, or pass through unknown types.

    MIME-type filtering is a first-pass hint; the DWG header check
    (AC1012–AC1032 bytes, §10.3 ¶4) is the real security boundary.
    Browsers/OSes report wildly different MIME for .dwg files, so
    we never block on MIME alone.
    """
    if not content_type:
        return None
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized in ALLOWED_DWG_MIME_TYPES or normalized in _BINARY_FALLBACK_MIME:
        return normalized
    # Pass-through for unknown MIME — DWG header check catches non-DWG files
    return normalized


# Minimum DWG file size in bytes — legitimate DWG files are never this small;
# the threshold prevents trivial DoS via header-only uploads.
# (ODA File Converter provides the real validation during conversion.)
MIN_DWG_SIZE_BYTES = 1024


def validate_dwg_header(first_chunk: bytes) -> None:
    """Validate that the file starts with a supported DWG version signature."""
    if len(first_chunk) < 6:
        raise AppHTTPException(415, "FILE_NOT_DWG", "File is too small to be a valid DWG.")
    header = first_chunk[:6]
    if header not in SUPPORTED_DWG_HEADERS:
        raise AppHTTPException(415, "FILE_NOT_DWG", "File does not have a valid DWG header.")


def build_storage_path(bucket: str, storage_key: str) -> Path:
    """Build a storage path and validate it stays inside the configured root (§6.2.6)."""
    root = settings.local_storage_root
    path = root / bucket / storage_key
    return ensure_within_root(root, path)


@lru_cache(maxsize=8)
def _get_storage_backend_cached(
    backend_name: str,
    local_root: str,
    minio_endpoint: str,
    minio_access_key: str,
    minio_secret_key: str,
) -> AbstractStorageBackend:
    if backend_name == "local":
        return LocalFileStorage(Path(local_root))
    if backend_name == "minio":
        try:
            return MinioStorage(
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
            )
        except StorageConfigurationError as exc:
            raise AppHTTPException(
                500,
                "STORAGE_BACKEND_MISCONFIGURED",
                "Configured storage backend is not ready.",
            ) from exc
    raise AppHTTPException(
        500,
        "STORAGE_BACKEND_UNSUPPORTED",
        f"Unsupported storage backend: {backend_name}",
    )


def get_storage_backend() -> AbstractStorageBackend:
    return _get_storage_backend_cached(
        settings.storage_backend,
        str(settings.local_storage_root),
        settings.minio_endpoint,
        settings.minio_access_key,
        settings.minio_secret_key,
    )


def clear_storage_backend_cache() -> None:
    _get_storage_backend_cached.cache_clear()


def storage_health() -> dict[str, str]:
    try:
        get_storage_backend().check_health()
        return {"status": "ok", "message": "Storage is reachable."}
    except (AppHTTPException, StorageError) as exc:
        logger.warning("Storage readiness check failed: %s", exc)
        return {"status": "error", "message": "Storage is unavailable."}


def _register_pending_storage_object(
    db: Session,
    storage: AbstractStorageBackend,
    bucket: str,
    storage_key: str,
    *,
    size_bytes: int,
    transfer_uid: str | None = None,
) -> None:
    pending = db.info.setdefault(_PENDING_STORAGE_OBJECTS, [])
    pending.append((storage, bucket, storage_key, size_bytes, transfer_uid))


def _discard_pending_storage_objects(db: Session) -> None:
    db.info.pop(_PENDING_STORAGE_OBJECTS, None)


def register_pending_destructive_transfer(
    db: Session,
    transfer_uid: str,
    *,
    transferred_bytes: int,
) -> None:
    """Settle an irreversible storage action only after the DB outcome is known."""
    pending = db.info.setdefault(_PENDING_DESTRUCTIVE_TRANSFERS, [])
    pending.append((transfer_uid, transferred_bytes))


def _settle_pending_destructive_transfers(db: Session, *, committed: bool) -> None:
    from app.services.file_transfer_service import session_factory_for, settle_transfer

    pending = db.info.pop(_PENDING_DESTRUCTIVE_TRANSFERS, [])
    for transfer_uid, transferred_bytes in pending:
        try:
            settle_transfer(
                session_factory_for(db),
                transfer_uid,
                status="succeeded" if committed else "compensation_required",
                transferred_bytes=transferred_bytes,
                error_code=None if committed else "PURGE_METADATA_COMMIT_FAILED",
                error_message=(
                    None
                    if committed
                    else "Objects were permanently removed but metadata did not commit."
                ),
            )
        except Exception:
            logger.exception(
                "Failed to settle destructive storage transfer %s after transaction end",
                transfer_uid,
            )


def _delete_pending_storage_objects(db: Session) -> None:
    from app.services.file_transfer_service import session_factory_for, settle_transfer

    pending = db.info.pop(_PENDING_STORAGE_OBJECTS, [])
    for storage, bucket, storage_key, size_bytes, transfer_uid in reversed(pending):
        status = "failed"
        error_code = "METADATA_TRANSACTION_ROLLED_BACK"
        error_message = "Stored object was removed after metadata transaction rollback."
        try:
            storage.delete_object(bucket, storage_key)
        except StorageError:
            status = "compensation_required"
            error_code = "STORAGE_COMPENSATION_REQUIRED"
            error_message = "Stored object could not be removed after metadata rollback."
            logger.exception(
                "Failed to compensate storage object after DB rollback: %s/%s",
                bucket,
                storage_key,
            )
        if transfer_uid:
            try:
                settle_transfer(
                    session_factory_for(db),
                    transfer_uid,
                    status=status,
                    transferred_bytes=size_bytes,
                    error_code=error_code,
                    error_message=error_message,
                )
            except Exception:
                logger.exception("Failed to settle rolled-back transfer %s", transfer_uid)


@event.listens_for(Session, "after_commit")
def _storage_after_commit(db: Session) -> None:
    _discard_pending_storage_objects(db)
    _settle_pending_destructive_transfers(db, committed=True)


@event.listens_for(Session, "after_rollback")
def _storage_after_rollback(db: Session) -> None:
    _delete_pending_storage_objects(db)
    _settle_pending_destructive_transfers(db, committed=False)


@event.listens_for(Session, "after_transaction_end")
def _storage_after_transaction_end(db: Session, transaction) -> None:
    if transaction.parent is None and not db.in_transaction():
        _delete_pending_storage_objects(db)
        _settle_pending_destructive_transfers(db, committed=False)


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
    is_excel = file_ext in (".xlsx", ".xls")
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
    storage = get_storage_backend()

    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    size = 0
    max_size = settings.max_upload_size_mb * 1024 * 1024

    with SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as tmp:
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
            if first:
                if file_ext == ".dwg":
                    validate_dwg_header(b"")
            if size == 0:
                raise AppHTTPException(
                    422,
                    "EMPTY_FILE",
                    "Uploaded file is empty — content must be at least 1 byte.",
                )
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
            from app.services.file_transfer_service import (
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
        from app.services.file_transfer_service import complete_transfer_in_transaction

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
    storage = get_storage_backend()
    auto_transfer = transfer_uid is None
    durable_intent = False
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
        from app.services.file_transfer_service import complete_transfer_in_transaction

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
    return build_storage_path(file.bucket, file.storage_key)
