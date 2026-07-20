from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from tempfile import SpooledTemporaryFile
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.modules.files.models import StoredFile
from app.modules.files.registration import save_bytes_as_file, save_upload_file
from app.modules.files.schemas import FileRead, ZipUploadResult
from app.modules.files.storage_transactions import (
    ACTIVE_TRANSFER_STATUSES,
    TransferSpec,
    complete_transfer_in_transaction,
    prepare_transfer_in_transaction,
    session_factory_for,
    settle_transfer,
)
from app.modules.files.validation import (
    sanitize_filename,
    validate_dwg_header,
    validate_upload_name,
)
from app.modules.identity.interface import CurrentUser
from app.modules.operations.audit.interface import write_audit_log
from app.platform.config.constants import ALLOWED_UPLOAD_EXTENSIONS
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.exceptions import AppHTTPException

router = APIRouter()

@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    batch_name: str = Query(""),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        max_length=128,
    ),
    db: Session = Depends(get_db),
):
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="inbound",
            operation="upload",
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
            original_name=sanitize_filename(upload.filename or "unnamed.dwg"),
        ),
    )
    db.commit()

    if transfer.status == "succeeded" and transfer.file_id is not None:
        stored = db.get(StoredFile, transfer.file_id)
        if stored is None:
            raise AppHTTPException(
                409,
                "IDEMPOTENT_RESULT_MISSING",
                "The previous upload result is no longer available.",
            )
        return ok(FileRead.model_validate(stored), request.state.request_id)
    if transfer.status not in ACTIVE_TRANSFER_STATUSES:
        raise AppHTTPException(
            409,
            "IDEMPOTENT_OPERATION_FAILED",
            "The previous operation with this idempotency key did not succeed.",
            {"transfer_uid": transfer.transfer_uid},
        )

    try:
        stored = await save_upload_file(
            db,
            upload,
            uploaded_by=current_user.id,
            batch_name=sanitize_filename(batch_name.strip()) if batch_name.strip() else None,
            transfer_uid=transfer.transfer_uid,
        )
        complete_transfer_in_transaction(
            db,
            transfer.transfer_uid,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            transferred_bytes=stored.size_bytes,
        )
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="files.upload",
            resource_type="file",
            resource_id=stored.id,
            after_json={"original_name": stored.original_name, "sha256": stored.sha256},
            request=request,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        detail = exc.detail if isinstance(exc, AppHTTPException) else None
        settle_transfer(
            session_factory_for(db),
            transfer.transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code=detail["code"] if isinstance(detail, dict) else "UPLOAD_TRANSACTION_FAILED",
            error_message=(
                detail["message"]
                if isinstance(detail, dict)
                else "Upload transaction failed before completion."
            ),
        )
        raise
    return ok(FileRead.model_validate(stored), request.state.request_id)


@router.post("/upload-zip", status_code=status.HTTP_201_CREATED)
async def upload_zip(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    file_ext: str = Query(".dwg", description="只提取此扩展名的文件 (.dwg 或 .dxf)"),
    db: Session = Depends(get_db),
):
    """Upload a .zip archive, extract matching files, and auto-create StoredFile records.

    The ZIP filename (minus .zip) becomes the batch_name for all extracted files.
    Only files matching ``file_ext`` are imported; others are counted as skipped.
    """
    import io
    import zipfile

    from app.platform.config.settings import settings

    # ── validate the upload is a .zip ──────────────────────────────────────
    zip_original = sanitize_filename(upload.filename or "unnamed.zip")
    if not zip_original.lower().endswith(".zip"):
        raise AppHTTPException(
            415,
            "FILE_TYPE_NOT_ALLOWED",
            "Only .zip archives are accepted by this endpoint.",
        )
    validate_upload_name(zip_original)  # 校验 .zip 在 ALLOWED_UPLOAD_EXTENSIONS 白名单内
    if not file_ext.strip():
        raise AppHTTPException(422, "INVALID_PARAMS", "file_ext query parameter is required.")
    target_ext = file_ext.strip().lower()
    if target_ext not in (".dwg", ".dxf"):
        raise AppHTTPException(
            422,
            "INVALID_PARAMS",
            "file_ext must be .dwg or .dxf.",
        )
    if target_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise AppHTTPException(
            422,
            "INVALID_PARAMS",
            f"Target extension {target_ext} is not allowed.",
        )

    # Derive batch_name from the ZIP filename
    batch_name = sanitize_filename(
        zip_original[: -len(".zip")]
        if zip_original.lower().endswith(".zip")
        else zip_original.rsplit(".", 1)[0]
    )
    if not batch_name or batch_name == "unnamed":
        batch_name = f"导入_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    # ── buffer the upload ──────────────────────────────────────────────────
    max_upload = settings.max_upload_size_mb * 1024 * 1024
    zip_bytes_total = 0
    with SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as tmp:
        while chunk := await upload.read(1024 * 1024):
            zip_bytes_total += len(chunk)
            if zip_bytes_total > max_upload:
                raise AppHTTPException(
                    413,
                    "FILE_TOO_LARGE",
                    f"ZIP archive exceeds max upload size ({settings.max_upload_size_mb} MB).",
                )
            tmp.write(chunk)
        tmp.seek(0)

        # ── extract ────────────────────────────────────────────────────────
        try:
            zf = zipfile.ZipFile(tmp, "r")
        except (zipfile.BadZipFile, io.UnsupportedOperation) as e:
            raise AppHTTPException(
                415,
                "FILE_NOT_ZIP",
                "The uploaded file is not a valid ZIP archive.",
            ) from e

        bad_names = zf.testzip()
        if bad_names is not None:
            raise AppHTTPException(
                415,
                "ZIP_CORRUPTED",
                f"ZIP archive contains corrupted entry: {bad_names}",
            )

        extracted: list[StoredFile] = []
        skipped = 0
        total_extracted_bytes = 0
        max_extract = settings.max_zip_extract_mb * 1024 * 1024
        entry_count = 0

        for info in zf.infolist():
            if info.is_dir():
                continue
            entry_count += 1
            if entry_count > settings.max_zip_entry_count:
                raise AppHTTPException(
                    413,
                    "ZIP_TOO_MANY_FILES",
                    f"ZIP archive contains more than {settings.max_zip_entry_count} files.",
                )

            # Path traversal defence: sanitize + strip any directory components
            entry_name = sanitize_filename(
                info.filename.rsplit("/", 1)[-1] if "/" in info.filename else info.filename
            )
            entry_ext = Path(entry_name).suffix.lower()
            if entry_ext != target_ext:
                skipped += 1
                continue

            # Read entry
            entry_bytes = zf.read(info)
            total_extracted_bytes += len(entry_bytes)
            if total_extracted_bytes > max_extract:
                raise AppHTTPException(
                    413,
                    "ZIP_TOO_LARGE",
                    f"Extracted content exceeds {settings.max_zip_extract_mb} MB limit.",
                )

            # DWG header validation
            if entry_ext == ".dwg":
                try:
                    validate_dwg_header(entry_bytes[:6])
                except AppHTTPException:
                    skipped += 1
                    continue

            # Persist via save_bytes_as_file (reuses existing storage logic)
            content_type = mimetypes.guess_type(entry_name)[0]
            bucket = (
                settings.minio_bucket_original
                if entry_ext == ".dwg"
                else settings.minio_bucket_dxf_original
            )
            storage_key = f"uploads/{uuid4().hex}{entry_ext}"
            stored = save_bytes_as_file(
                db,
                bucket=bucket,
                storage_key=storage_key,
                original_name=entry_name,
                file_ext=entry_ext,
                content_type=content_type or "application/octet-stream",
                payload=entry_bytes,
                uploaded_by=current_user.id,
                batch_name=batch_name,
                transfer_direction="inbound",
                transfer_operation="upload_zip",
                request_id=request.state.request_id,
            )
            extracted.append(stored)

    if not extracted and skipped == 0:
        raise AppHTTPException(
            422,
            "ZIP_EMPTY",
            "The ZIP archive contains no files.",
        )

    # ── audit log ──────────────────────────────────────────────────────────
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.upload_zip",
        resource_type="file",
        resource_id=0,
        after_json={
            "batch_name": batch_name,
            "target_ext": target_ext,
            "success_count": len(extracted),
            "skipped_count": skipped,
            "zip_original": zip_original,
            "zip_size": zip_bytes_total,
        },
        request=request,
    )
    db.commit()

    return ok(
        ZipUploadResult(
            batch_name=batch_name,
            files=[FileRead.model_validate(f) for f in extracted],
            success_count=len(extracted),
            skipped_count=skipped,
        ).model_dump(),
        request.state.request_id,
    )
