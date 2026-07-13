"""Bounded DXF inspection and safe SVG preview rendering.

The renderer intentionally uses ezdxf's recording SVG backend instead of
Matplotlib.  It renders the document once, keeps CAD geometry crisp while the
browser zooms, and does not require a GUI stack.  Object persistence and the
MySQL transfer ledger are added by the cache functions below this pure render
boundary.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from uuid import uuid4

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout
from ezdxf.addons.drawing.config import (
    BackgroundPolicy,
    ColorPolicy,
    Configuration,
    ImagePolicy,
)
from ezdxf.addons.drawing.svg import SVGBackend
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile
from app.services.file_transfer_service import (
    TransferSpec,
    complete_reused_transfer_in_transaction,
    complete_transfer_in_transaction,
    mark_transfer_in_progress,
    prepare_transfer_in_transaction,
    session_factory_for,
    settle_transfer,
)
from app.services.storage_service import sanitize_filename, save_bytes_as_file
from app.storage.base import (
    AbstractStorageBackend,
    StorageError,
    StorageObjectNotFound,
)

logger = logging.getLogger(__name__)

MAX_DXF_SIZE_BYTES = 20 * 1024 * 1024
MAX_DXF_ENTITIES = 100_000
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
PREVIEW_BACKGROUND = "#111827"
PREVIEW_MAX_WIDTH_MM = 400.0
PREVIEW_MAX_HEIGHT_MM = 300.0
PREVIEW_RENDERER_VERSION = "svg-v1"

# SVG is produced by ezdxf, never accepted from the user.  This final invariant
# still prevents a future backend/config change from turning a preview into an
# active or externally-referencing document.
FORBIDDEN_SVG_TOKENS = (
    b"<script",
    b"<foreignobject",
    b"href=",
    b"xlink:",
    b"<!doctype",
    b"<!entity",
)


@dataclass(frozen=True)
class DxfBounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float


@dataclass(frozen=True)
class InspectedDxf:
    document: Any
    document_entities: int
    modelspace_entities: int
    entity_counts: dict[str, int]
    layers: tuple[str, ...]
    layer_colors: dict[str, int]


@dataclass(frozen=True)
class RenderedDxfPreview:
    payload: bytes
    content_type: str
    document_entities: int
    modelspace_entities: int
    entity_counts: dict[str, int]
    layers: tuple[str, ...]
    layer_colors: dict[str, int]
    bounds: DxfBounds


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
    return AppHTTPException(status_code, code, message)


def validate_dxf_source_size(size_bytes: int) -> None:
    if size_bytes < 1:
        raise _error(422, "DXF_EMPTY", "DXF 文件为空，无法生成预览。")
    if size_bytes > MAX_DXF_SIZE_BYTES:
        raise _error(
            413,
            "DXF_TOO_LARGE",
            f"DXF 文件超过在线预览上限 {MAX_DXF_SIZE_BYTES // (1024 * 1024)} MB。",
        )


def _read_dxf_document(payload: bytes) -> Any:
    validate_dxf_source_size(len(payload))
    path: Path | None = None
    try:
        with NamedTemporaryFile(suffix=".dxf", delete=False) as temporary:
            temporary.write(payload)
            path = Path(temporary.name)
        return ezdxf.readfile(path)
    except Exception as exc:
        logger.info("DXF preview parse rejected input: %s", exc.__class__.__name__)
        raise _error(
            415,
            "DXF_PARSE_ERROR",
            "DXF 文件解析失败，请确认文件有效且未损坏。",
        ) from exc
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def inspect_dxf(payload: bytes) -> InspectedDxf:
    document = _read_dxf_document(payload)
    living_entities = [
        entity for entity in document.entitydb.values() if getattr(entity, "is_alive", True)
    ]
    document_entities = len(living_entities)
    if document_entities > MAX_DXF_ENTITIES:
        raise _error(
            413,
            "DXF_TOO_COMPLEX",
            f"DXF 文档实体数超过在线预览上限 {MAX_DXF_ENTITIES:,}。",
        )

    modelspace = document.modelspace()
    entity_counts = Counter(entity.dxftype() for entity in modelspace)
    modelspace_entities = sum(entity_counts.values())
    layers = tuple(layer.dxf.name for layer in document.layers)
    layer_colors = {
        layer.dxf.name: int(getattr(layer.dxf, "color", 7) or 7) for layer in document.layers
    }
    return InspectedDxf(
        document=document,
        document_entities=document_entities,
        modelspace_entities=modelspace_entities,
        entity_counts=dict(sorted(entity_counts.items())),
        layers=layers,
        layer_colors=layer_colors,
    )


def _finite(value: float, fallback: float = 0.0) -> float:
    return float(value) if math.isfinite(float(value)) else fallback


def _bounds_from_box(box: Any) -> DxfBounds:
    if not getattr(box, "has_data", False):
        return DxfBounds(0.0, 0.0, 0.0, 0.0)
    return DxfBounds(
        min_x=_finite(box.extmin.x),
        min_y=_finite(box.extmin.y),
        max_x=_finite(box.extmax.x),
        max_y=_finite(box.extmax.y),
    )


def render_inspected_dxf_to_svg(inspected: InspectedDxf) -> RenderedDxfPreview:
    backend = SVGBackend()
    configuration = Configuration(
        background_policy=BackgroundPolicy.CUSTOM,
        color_policy=ColorPolicy.COLOR,
        custom_bg_color=PREVIEW_BACKGROUND,
        image_policy=ImagePolicy.IGNORE,
        hatching_timeout=2.0,
        circle_approximation_count=64,
        max_flattening_distance=0.1,
    )
    try:
        Frontend(
            RenderContext(inspected.document),
            backend,
            config=configuration,
        ).draw_layout(inspected.document.modelspace())
        render_box = backend.player().bbox()
        bounds = _bounds_from_box(render_box)
        svg_text = backend.get_string(
            layout.Page(
                0,
                0,
                max_width=PREVIEW_MAX_WIDTH_MM,
                max_height=PREVIEW_MAX_HEIGHT_MM,
            ),
            render_box=render_box,
            xml_declaration=True,
        )
    except AppHTTPException:
        raise
    except Exception as exc:
        logger.warning("DXF SVG rendering failed", exc_info=True)
        raise _error(
            422,
            "DXF_RENDER_ERROR",
            "DXF 文件无法渲染为在线预览。",
        ) from exc

    svg = svg_text.encode("utf-8")
    if len(svg) > MAX_PREVIEW_BYTES:
        raise _error(
            413,
            "DXF_PREVIEW_TOO_LARGE",
            f"DXF 预览结果超过上限 {MAX_PREVIEW_BYTES // (1024 * 1024)} MB。",
        )
    lower = svg.lower()
    if any(token in lower for token in FORBIDDEN_SVG_TOKENS):
        raise _error(
            422,
            "DXF_PREVIEW_UNSAFE",
            "DXF 预览包含不允许的外部或活动内容。",
        )

    return RenderedDxfPreview(
        payload=svg,
        content_type="image/svg+xml",
        document_entities=inspected.document_entities,
        modelspace_entities=inspected.modelspace_entities,
        entity_counts=inspected.entity_counts,
        layers=inspected.layers,
        layer_colors=inspected.layer_colors,
        bounds=bounds,
    )


def render_dxf_to_svg(payload: bytes) -> RenderedDxfPreview:
    return render_inspected_dxf_to_svg(inspect_dxf(payload))


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
