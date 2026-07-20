"""DXF preview cache, transfer ledger and file-registry integration."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.cad_processing.preview_rendering import (
    MAX_DXF_SIZE_BYTES,
    PREVIEW_RENDERER_VERSION,
    DxfBounds,
    InspectedDxf,
    inspect_dxf,
    preview_error,
    render_inspected_dxf_to_svg,
    validate_dxf_source_size,
)
from app.modules.files.interface import (
    StoredFile,
    TransferSpec,
    complete_reused_transfer_in_transaction,
    complete_transfer_in_transaction,
    mark_transfer_in_progress,
    prepare_transfer_in_transaction,
    sanitize_filename,
    save_bytes_as_file,
    session_factory_for,
    settle_transfer,
)
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import (
    AbstractStorageBackend,
    StorageError,
    StorageObjectNotFound,
)

__all__ = [
    "MAX_DXF_SIZE_BYTES",
    "DxfPreviewAsset",
    "get_or_create_dxf_preview",
    "invalidate_dxf_previews_for_source",
    "preview_batch_name",
    "validate_dxf_source_size",
]


@dataclass(frozen=True)
class DxfPreviewAsset:
    preview_file: StoredFile
    cached: bool
    document_entities: int
    modelspace_entities: int
    entity_counts: dict[str, int]
    layers: tuple[str, ...]
    layer_colors: dict[str, int]
    bounds: DxfBounds


def _error(status_code: int, code: str, message: str) -> AppHTTPException:
    return preview_error(status_code, code, message)


def preview_batch_name(source: StoredFile) -> str:
    if source.id is None:
        raise ValueError("Source file must be persisted before preview generation.")
    return f"dxf-preview:{PREVIEW_RENDERER_VERSION}:{source.id}:{source.sha256[:16]}"


def _header_bounds(document: Any) -> DxfBounds:
    try:
        extmin = document.header.get("$EXTMIN")
        extmax = document.header.get("$EXTMAX")
        values = tuple(float(value) for value in (*extmin[:2], *extmax[:2]))
        if (
            all(math.isfinite(value) and abs(value) < 1e19 for value in values)
            and values[2] >= values[0]
            and values[3] >= values[1]
        ):
            return DxfBounds(values[0], values[1], values[2], values[3])
    except (AttributeError, IndexError, TypeError, ValueError):
        pass
    return DxfBounds(0.0, 0.0, 0.0, 0.0)


def _asset_from_inspection(
    preview_file: StoredFile,
    inspected: InspectedDxf,
    *,
    cached: bool,
    bounds: DxfBounds | None = None,
) -> DxfPreviewAsset:
    return DxfPreviewAsset(
        preview_file=preview_file,
        cached=cached,
        document_entities=inspected.document_entities,
        modelspace_entities=inspected.modelspace_entities,
        entity_counts=inspected.entity_counts,
        layers=inspected.layers,
        layer_colors=inspected.layer_colors,
        bounds=bounds or _header_bounds(inspected.document),
    )


def _invalidate_preview_file(
    db: Session,
    preview: StoredFile,
    *,
    request_id: str,
    actor_user_id: int | None = None,
) -> None:
    if preview.status == "deleted":
        return
    preview.status = "deleted"
    preview.deleted_at = datetime.now(UTC)
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="internal",
            operation="preview_invalidate",
            actor_user_id=actor_user_id,
            request_id=request_id,
            file_id=preview.id,
            batch_ref=preview.batch_name,
            bucket=preview.bucket,
            storage_key=preview.storage_key,
            original_name=preview.original_name,
            expected_bytes=preview.size_bytes,
        ),
    )
    complete_transfer_in_transaction(
        db,
        transfer.transfer_uid,
        file_id=preview.id,
        bucket=preview.bucket,
        storage_key=preview.storage_key,
        original_name=preview.original_name,
        transferred_bytes=0,
    )


def invalidate_dxf_previews_for_source(
    db: Session,
    source: StoredFile,
    *,
    actor_user_id: int,
    request_id: str,
) -> int:
    """Soft-delete active preview registrations for one persisted DXF source."""
    if source.file_ext.lower() != ".dxf":
        return 0
    previews = db.scalars(
        select(StoredFile).where(
            StoredFile.batch_name == preview_batch_name(source),
            StoredFile.file_ext == ".svg",
            StoredFile.status != "deleted",
        )
    ).all()
    for preview in previews:
        _invalidate_preview_file(
            db,
            preview,
            request_id=request_id,
            actor_user_id=actor_user_id,
        )
    return len(previews)


def _find_cached_preview(
    db: Session,
    source: StoredFile,
    storage: AbstractStorageBackend,
    *,
    request_id: str,
) -> StoredFile | None:
    candidates = db.scalars(
        select(StoredFile)
        .where(
            StoredFile.batch_name == preview_batch_name(source),
            StoredFile.file_ext == ".svg",
            StoredFile.status != "deleted",
        )
        .order_by(StoredFile.id.desc())
    ).all()
    for candidate in candidates:
        try:
            object_info = storage.stat_object(candidate.bucket, candidate.storage_key)
        except StorageObjectNotFound:
            _invalidate_preview_file(db, candidate, request_id=request_id)
            continue
        except StorageError as exc:
            raise _error(
                503,
                "DXF_PREVIEW_STORAGE_UNAVAILABLE",
                "预览存储暂时不可用。",
            ) from exc
        if object_info.size_bytes != candidate.size_bytes:
            _invalidate_preview_file(db, candidate, request_id=request_id)
            continue
        return candidate
    return None


def get_or_create_dxf_preview(
    db: Session,
    source: StoredFile,
    payload: bytes,
    *,
    storage: AbstractStorageBackend,
    request_id: str,
) -> DxfPreviewAsset:
    """Return a registered preview, rendering and storing it only on cache miss.

    Rendering happens before the source-row lock.  A second cache check under
    the lock prevents concurrent requests from writing duplicate objects while
    keeping the database connection out of the CPU-heavy render phase.
    """
    validate_dxf_source_size(source.size_bytes)
    inspected = inspect_dxf(payload)
    cached = _find_cached_preview(
        db,
        source,
        storage,
        request_id=request_id,
    )
    if cached is not None:
        return _asset_from_inspection(cached, inspected, cached=True)

    stem = source.original_name.rsplit(".", 1)[0]
    preview_name = sanitize_filename(f"{stem}_预览.svg")
    batch_name = preview_batch_name(source)
    bucket = settings.minio_bucket_reports
    storage_key = (
        f"previews/dxf/{PREVIEW_RENDERER_VERSION}/{source.id}/"
        f"{source.sha256[:16]}/{uuid4().hex}.svg"
    )

    # Persist the transfer intent before rendering or starting the source-row
    # locking transaction.  This ordering is required by MySQL REPEATABLE READ:
    # the caller's transaction must be able to see the transfer row later when
    # it completes the metadata write.
    transfer = prepare_transfer_in_transaction(
        db,
        TransferSpec(
            direction="internal",
            operation="preview_generate",
            actor_user_id=None,
            request_id=request_id,
            batch_ref=batch_name,
            bucket=bucket,
            storage_key=storage_key,
            original_name=preview_name,
        ),
    )
    factory = session_factory_for(db)
    db.commit()
    try:
        rendered = render_inspected_dxf_to_svg(inspected)
        mark_transfer_in_progress(
            factory,
            transfer.transfer_uid,
            bucket=bucket,
            storage_key=storage_key,
            expected_bytes=len(rendered.payload),
        )
        locked_source = db.scalar(
            select(StoredFile).where(StoredFile.id == source.id).with_for_update()
        )
        if (
            locked_source is None
            or locked_source.status == "deleted"
            or locked_source.sha256 != source.sha256
        ):
            raise _error(
                409,
                "DXF_SOURCE_CHANGED",
                "DXF 文件在生成预览期间已变更或删除，请刷新后重试。",
            )

        cached = _find_cached_preview(
            db,
            locked_source,
            storage,
            request_id=request_id,
        )
        if cached is not None:
            complete_reused_transfer_in_transaction(
                db,
                transfer.transfer_uid,
                operation="preview_cache_reuse",
                file_id=cached.id,
                bucket=cached.bucket,
                storage_key=cached.storage_key,
                original_name=cached.original_name,
            )
            return _asset_from_inspection(
                cached,
                inspected,
                cached=True,
                bounds=rendered.bounds,
            )

        preview_file = save_bytes_as_file(
            db,
            bucket=bucket,
            storage_key=storage_key,
            original_name=preview_name,
            file_ext=".svg",
            content_type=rendered.content_type,
            payload=rendered.payload,
            uploaded_by=None,
            batch_name=batch_name,
            transfer_uid=transfer.transfer_uid,
        )
        complete_transfer_in_transaction(
            db,
            transfer.transfer_uid,
            file_id=preview_file.id,
            bucket=preview_file.bucket,
            storage_key=preview_file.storage_key,
            original_name=preview_file.original_name,
            transferred_bytes=preview_file.size_bytes,
        )
        return _asset_from_inspection(
            preview_file,
            inspected,
            cached=False,
            bounds=rendered.bounds,
        )
    except Exception as exc:
        db.rollback()
        detail = exc.detail if isinstance(exc, AppHTTPException) else None
        settle_transfer(
            factory,
            transfer.transfer_uid,
            status="failed",
            transferred_bytes=0,
            error_code=(
                detail["code"] if isinstance(detail, dict) else "PREVIEW_TRANSACTION_FAILED"
            ),
            error_message=(
                detail["message"]
                if isinstance(detail, dict)
                else "DXF preview transaction failed before completion."
            ),
        )
        raise
