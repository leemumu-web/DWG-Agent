"""Registered-file lifecycle transitions."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.modules.files.models import StoredFile
from app.modules.files.storage_transactions import (
    TransferSpec,
    complete_transfer_in_transaction,
    prepare_transfer_in_transaction,
)


def soft_delete_file_in_transaction(
    db: Session,
    stored: StoredFile,
    *,
    actor_user_id: int,
    request_id: str,
    batch_ref: str | None = None,
) -> None:
    # Temporary adapter seam until CAD preview moves into its own domain module.
    from app.services.dxf_preview_service import invalidate_dxf_previews_for_source

    invalidate_dxf_previews_for_source(
        db,
        stored,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    stored.status = "deleted"
    stored.deleted_at = datetime.now(UTC)
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="internal",
            operation="soft_delete",
            actor_user_id=actor_user_id,
            request_id=request_id,
            file_id=stored.id,
            batch_ref=batch_ref,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            expected_bytes=stored.size_bytes,
        ),
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


__all__ = ["soft_delete_file_in_transaction"]
