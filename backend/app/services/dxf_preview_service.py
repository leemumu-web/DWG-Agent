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
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import ezdxf
from ezdxf.addons.drawing import Frontend, RenderContext, layout
from ezdxf.addons.drawing.config import (
    BackgroundPolicy,
    ColorPolicy,
    Configuration,
    ImagePolicy,
)
from ezdxf.addons.drawing.svg import SVGBackend

from app.core.exceptions import AppHTTPException

logger = logging.getLogger(__name__)

MAX_DXF_SIZE_BYTES = 20 * 1024 * 1024
MAX_DXF_ENTITIES = 100_000
MAX_PREVIEW_BYTES = 16 * 1024 * 1024
PREVIEW_BACKGROUND = "#111827"
PREVIEW_MAX_WIDTH_MM = 400.0
PREVIEW_MAX_HEIGHT_MM = 300.0

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


def render_dxf_to_svg(payload: bytes) -> RenderedDxfPreview:
    inspected = inspect_dxf(payload)
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
