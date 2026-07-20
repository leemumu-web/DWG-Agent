"""Excel upload persistence built on the files module's durable transfer saga."""

from __future__ import annotations

from fastapi import Request, UploadFile
from sqlalchemy.orm import Session

from app.modules.files.interface import (
    ACTIVE_TRANSFER_STATUSES,
    StoredFile,
    TransferSpec,
    complete_transfer_in_transaction,
    prepare_transfer_in_transaction,
    sanitize_filename,
    save_upload_file,
    session_factory_for,
    settle_transfer,
)
from app.modules.identity.interface import CurrentUser
from app.platform.http.exceptions import AppHTTPException


async def store_excel_upload(
    db: Session,
    upload: UploadFile,
    *,
    current_user: CurrentUser,
    request: Request,
    idempotency_key: str | None = None,
) -> tuple[StoredFile, bool]:
    """Persist an Excel upload with the same durable saga used by `/files`."""
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="inbound",
            operation="upload",
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
            idempotency_key=idempotency_key,
            original_name=sanitize_filename(upload.filename or "unnamed.xlsx"),
        ),
    )
    db.commit()
    if transfer.status == "succeeded" and transfer.file_id is not None:
        stored = db.get(StoredFile, transfer.file_id)
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(
                409,
                "IDEMPOTENT_RESULT_MISSING",
                "The previous upload result is no longer available.",
            )
        return stored, True
    if transfer.status not in ACTIVE_TRANSFER_STATUSES:
        raise AppHTTPException(
            409,
            "IDEMPOTENT_OPERATION_FAILED",
            "The previous upload with this idempotency key did not succeed.",
            {"transfer_uid": transfer.transfer_uid},
        )
    try:
        stored = await save_upload_file(
            db,
            upload,
            uploaded_by=current_user.id,
            transfer_uid=transfer.transfer_uid,
            request_id=request.state.request_id,
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
        return stored, False
    except Exception as exc:
        db.rollback()
        detail = exc.detail if isinstance(exc, AppHTTPException) else None
        settle_transfer(
            session_factory_for(db),
            transfer.transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code=(
                detail["code"] if isinstance(detail, dict) else "UPLOAD_TRANSACTION_FAILED"
            ),
            error_message=(
                detail["message"]
                if isinstance(detail, dict)
                else "Upload transaction failed before completion."
            ),
        )
        raise


__all__ = ["store_excel_upload"]
