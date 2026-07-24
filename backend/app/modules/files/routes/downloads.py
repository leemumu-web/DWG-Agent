from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.access import require_file_download_access
from app.modules.files.exports import (
    build_signed_download_url,
    build_zip_to_path,
    download_headers,
    preview_zip_availability,
    validate_download_signature,
)
from app.modules.files.models import StoredFile
from app.modules.files.schemas import ZipDownloadRequest
from app.modules.files.storage_transactions import (
    TransferSpec,
    prepare_transfer_in_transaction,
    session_factory_for,
    settle_stream,
)
from app.modules.files.validation import sanitize_filename
from app.modules.identity.interface import CurrentUser
from app.modules.operations.audit.interface import write_audit_log
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException, not_found
from app.platform.storage import factory as storage_factory
from app.platform.storage.base import StorageError, StorageObjectNotFound

static_router = APIRouter()
item_router = APIRouter()


@item_router.get("/{file_id}/download-url")
def get_download_url(
    file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    require_file_download_access(db, current_user, stored)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.download_url",
        resource_type="file",
        resource_id=stored.id,
        request=request,
    )
    db.commit()
    return ok(build_signed_download_url(file_id), request.state.request_id)


@item_router.get("/{file_id}/download")
def download_file(
    file_id: int,
    request: Request,
    current_user: CurrentUser,
    expires: int | None = Query(default=None),
    signature: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    require_file_download_access(db, current_user, stored)
    if expires is None or signature is None:
        raise AppHTTPException(
            403, "INVALID_DOWNLOAD_SIGNATURE", "Download URL signature is required."
        )
    validate_download_signature(file_id, expires, signature)
    storage = storage_factory.get_storage_backend()
    try:
        object_info = storage.stat_object(stored.bucket, stored.storage_key)
    except StorageObjectNotFound:
        raise not_found("StoredFileObject") from None
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "STORAGE_READ_FAILED",
            "Failed to read stored file object.",
        ) from exc

    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="outbound",
            operation="download",
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
            idempotency_key=request.state.request_id,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            expected_bytes=object_info.size_bytes,
        ),
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.download",
        resource_type="file",
        resource_id=stored.id,
        request=request,
    )
    db.commit()
    factory = session_factory_for(db)
    return StreamingResponse(
        settle_stream(
            factory,
            transfer.transfer_uid,
            storage.iter_file(stored.bucket, stored.storage_key),
        ),
        media_type=stored.content_type or "application/octet-stream",
        headers={
            **download_headers(stored.original_name),
            "Content-Length": str(object_info.size_bytes),
        },
    )


# ── bulk operations ──────────────────────────────────────────────────────────

@static_router.post("/download-zip/preview")
def preview_zip_endpoint(
    request: Request,
    payload: ZipDownloadRequest,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Report whether every requested export format exists before ZIP creation."""
    requested_ids = list(dict.fromkeys(payload.file_ids))
    if not requested_ids:
        raise AppHTTPException(422, "INVALID_PARAMS", "file_ids must not be empty.")

    stored_list = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.id.in_(requested_ids), StoredFile.status != "deleted"
            )
        ).all()
    )
    if len(stored_list) != len(requested_ids):
        raise not_found("File")
    for stored in stored_list:
        require_file_download_access(db, current_user, stored)

    preview = preview_zip_availability(db, requested_ids, payload.formats)
    return ok(preview, request.state.request_id)


@static_router.post("/download-zip")
def download_zip_endpoint(
    request: Request,
    payload: ZipDownloadRequest,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    """Build a zip archive with selected files' DWG and/or DXF versions,
    stream it directly, and clean up the temp file on completion."""
    if not payload.file_ids:
        raise AppHTTPException(422, "INVALID_PARAMS", "file_ids must not be empty.")
    if not payload.formats:
        raise AppHTTPException(
            422, "INVALID_PARAMS", "formats must not be empty — choose at least dwg or dxf."
        )

    # Verify access
    stored_list = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.id.in_(payload.file_ids), StoredFile.status != "deleted"
            )
        ).all()
    )
    if len(stored_list) != len(payload.file_ids):
        raise not_found("File")
    for s in stored_list:
        require_file_download_access(db, current_user, s)

    clean_name = sanitize_filename(payload.folder_name) or "图纸导出"
    prepared = build_zip_to_path(db, payload.file_ids, payload.formats, clean_name)
    try:
        transfer = prepare_transfer_in_transaction(
            db,
            TransferSpec(
                direction="outbound",
                operation="download_zip",
                actor_user_id=current_user.id,
                request_id=request.state.request_id,
                idempotency_key=request.state.request_id,
                batch_ref=request.state.request_id,
                original_name=prepared.filename,
                expected_bytes=prepared.size_bytes,
            ),
        )
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="files.download_zip",
            resource_type="file",
            resource_id=0,
            after_json={
                "file_ids": payload.file_ids,
                "formats": payload.formats,
                "folder": clean_name,
            },
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        prepared.path.unlink(missing_ok=True)
        raise

    def _stream_and_cleanup():
        try:
            with prepared.path.open("rb") as f:
                while chunk := f.read(1024 * 1024):  # 1 MiB chunks
                    yield chunk
        finally:
            prepared.path.unlink(missing_ok=True)

    encoded_filename = quote(f"{clean_name}.zip")
    return StreamingResponse(
        settle_stream(
            session_factory_for(db),
            transfer.transfer_uid,
            _stream_and_cleanup(),
        ),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(prepared.size_bytes),
        },
    )
