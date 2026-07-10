"""DXF → PNG preview renderer.

Thread-safe, object-oriented Matplotlib rendering with ezdxf LayoutProperties
for dark-background CAD-style output. No global pyplot state.

Uses the same storage abstraction as the rest of the platform — reads from
local FS or MinIO, writes cached PNGs back to the same backend.
"""

from __future__ import annotations

import gc
import io
import logging
from typing import Any

import ezdxf
import matplotlib
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

# Must be set before any other matplotlib import / usage
matplotlib.use("Agg")

from app.storage.base import AbstractStorageBackend

logger = logging.getLogger(__name__)

MAX_DXF_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_ENTITIES = 100_000
PREVIEW_DPI = 200
PREVIEW_MAX_FIG_INCHES = 20.0
BUCKET = "dwg-derived"  # reuse the same bucket as conversion results


def _count_entities_and_layers(
    msp: Any, doc: ezdxf.document.Drawing,
) -> tuple[dict[str, int], list[str], dict[str, int]]:
    """Count entities by type and collect layer names / colors."""
    entity_counts: dict[str, int] = {}
    total = 0
    for entity in msp:
        total += 1
        if total > MAX_ENTITIES:
            break  # stop counting — caller will reject
        etype = entity.dxftype()
        entity_counts[etype] = entity_counts.get(etype, 0) + 1

    layers = [layer.dxf.name for layer in doc.layers]
    layer_colors: dict[str, int] = {}
    for layer in doc.layers:
        layer_colors[layer.dxf.name] = layer.dxf.color

    return entity_counts, layers, layer_colors


def _safe_bounds(doc: ezdxf.document.Drawing) -> dict[str, float]:
    """Compute modelspace bounding box, falling back to header extents on error."""
    try:
        bbox = ezdxf.bbox.extents(doc.modelspace(), cache=ezdxf.bbox.Cache())
        if bbox.has_data:
            return {
                "min_x": float(bbox.extmin.x),
                "min_y": float(bbox.extmin.y),
                "max_x": float(bbox.extmax.x),
                "max_y": float(bbox.extmax.y),
            }
    except Exception:
        logger.warning("bbox.extents failed — using defaults", exc_info=True)

    # Fallback: header extents
    try:
        emax = doc.header.get("$EXTMAX", (100, 100, 0))
        return {"min_x": 0.0, "min_y": 0.0, "max_x": float(emax[0]), "max_y": float(emax[1])}
    except Exception:
        return {"min_x": 0.0, "min_y": 0.0, "max_x": 100.0, "max_y": 100.0}


def render_dxf_to_png_bytes(doc: ezdxf.document.Drawing) -> bytes:
    """Render a DXF document to PNG bytes using thread-safe Matplotlib OO API.

    No global pyplot state — each call creates its own Figure.
    Dark background via LayoutProperties for CAD-native appearance.
    """
    msp = doc.modelspace()

    # ── figure sizing (auto-scale to fit bounds) ────────────────────
    bounds = _safe_bounds(doc)
    ww = max(bounds["max_x"] - bounds["min_x"], 1.0)
    wh = max(bounds["max_y"] - bounds["min_y"], 1.0)

    if ww > wh:
        fig_w = PREVIEW_MAX_FIG_INCHES
        fig_h = max(2.0, PREVIEW_MAX_FIG_INCHES * wh / ww)
    else:
        fig_h = PREVIEW_MAX_FIG_INCHES
        fig_w = max(2.0, PREVIEW_MAX_FIG_INCHES * ww / wh)

    # ── thread-safe OO rendering ────────────────────────────────────
    fig = Figure(dpi=PREVIEW_DPI, figsize=(fig_w, fig_h))
    canvas = FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_xlim(bounds["min_x"], bounds["max_x"])
    ax.set_ylim(bounds["min_y"], bounds["max_y"])
    ax.set_aspect("equal")
    ax.axis("off")

    ctx = RenderContext(doc)
    out = MatplotlibBackend(ax)

    # Dark background — ACI colors auto-adapt for visibility on dark canvas
    config = Configuration(
        background_policy=BackgroundPolicy.CUSTOM,
        color_policy=ColorPolicy.COLOR,
        custom_bg_color="#1a1a2e",
    )

    Frontend(ctx, out, config=config).draw_layout(msp, finalize=True)

    # ── render to bytes ─────────────────────────────────────────────
    canvas.draw()
    buf = io.BytesIO()
    fig.savefig(
        buf, format="png", dpi=PREVIEW_DPI,
        bbox_inches="tight", pad_inches=0.1,
        facecolor="#1a1a2e", edgecolor="none",
    )

    # ── explicit cleanup — prevent memory leaks ─────────────────────
    ax.clear()
    fig.clf()
    fig.clear()
    del ctx, out, config, ax, canvas, fig
    gc.collect()

    return buf.getvalue()


def preview_dxf(
    file_id: int,
    original_name: str,
    sha256: str,
    dxf_bytes: bytes,
    storage: AbstractStorageBackend,
) -> dict[str, Any]:
    """Full DXF preview pipeline.

    1. Parse & validate DXF
    2. Extract metadata (layers, entity counts, bounds)
    3. Check for cached PNG in storage
    4. Render if missing → store PNG
    5. Generate signed download URL for the PNG
    6. Return preview metadata dict
    """
    # ── size guard ──────────────────────────────────────────────────
    if len(dxf_bytes) > MAX_DXF_SIZE_BYTES:
        raise _preview_error(
            "DXF_TOO_LARGE",
            f"DXF 文件过大（{len(dxf_bytes) / 1024 / 1024:.1f} MB），上限 {MAX_DXF_SIZE_BYTES / 1024 / 1024:.0f} MB，无法在线预览",
        )

    # ── parse ───────────────────────────────────────────────────────
    # Use readfile with a temp file — ezdxf's read(io.BytesIO()) can fail
    # on certain DXF files because it extracts the directory from the file
    # path for relative xref / font resolution. readfile(str_path) is the
    # most robust codepath.
    import os
    import tempfile

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(dxf_bytes)
            tmp_path = tmp.name
        doc = ezdxf.readfile(tmp_path)
    except Exception as exc:
        logger.warning("DXF parse error for file_id=%s: %s", file_id, exc)
        raise _preview_error(
            "DXF_PARSE_ERROR",
            "DXF 文件解析失败，无法生成预览。请检查文件是否有效。",
        ) from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    msp = doc.modelspace()

    # ── entity count guard ──────────────────────────────────────────
    entity_counts, layers, layer_colors = _count_entities_and_layers(msp, doc)
    total_entities = sum(entity_counts.values())
    if total_entities > MAX_ENTITIES:
        raise _preview_error(
            "DXF_TOO_COMPLEX",
            f"DXF 包含 {total_entities} 个实体（上限 {MAX_ENTITIES}），无法在线预览",
        )

    bounds = _safe_bounds(doc)

    # ── cache lookup ────────────────────────────────────────────────
    cache_key = f"previews/{file_id}_{sha256[:8]}.png"
    try:
        if storage.local_path(BUCKET, cache_key) is not None:
            local = storage.local_path(BUCKET, cache_key)
            if local is not None and local.exists():
                logger.info("dxf_preview_cache_hit file_id=%d", file_id)
                from app.services.file_service import build_signed_download_url

                url_info = build_signed_download_url(file_id)
                return {
                    "file_id": file_id,
                    "file_name": original_name,
                    "preview_url": url_info.url,
                    "entity_counts": entity_counts,
                    "total_entities": total_entities,
                    "layers": layers,
                    "layer_colors": {k: v for k, v in layer_colors.items()},
                    "bounds": bounds,
                    "cached": True,
                }
    except Exception:
        logger.debug("dxf_preview_cache_miss file_id=%d", file_id)

    # ── render ─────────────────────────────────────────────────────
    try:
        png_bytes = render_dxf_to_png_bytes(doc)
    except Exception as exc:
        logger.warning("DXF render error for file_id=%s: %s", file_id, exc)
        raise _preview_error(
            "DXF_RENDER_ERROR",
            "DXF 渲染为 PNG 图片失败，无法生成预览。",
        ) from exc

    # ── store PNG ──────────────────────────────────────────────────

    from app.db.session import SessionLocal
    from app.services.storage_service import save_bytes_as_file

    png_name = original_name.rsplit(".", 1)[0] + ".png" if "." in original_name else original_name + ".png"

    # Use one-off DB session for cache write
    cache_db = SessionLocal()
    try:
        save_bytes_as_file(
            cache_db,
            bucket=BUCKET,
            storage_key=cache_key,
            original_name=png_name,
            file_ext=".png",
            content_type="image/png",
            payload=png_bytes,
            uploaded_by=None,  # system-generated
            batch_name="dxf-previews",
        )
        cache_db.commit()
    except Exception:
        cache_db.rollback()
        logger.warning("dxf_preview_cache_write_failed file_id=%d", file_id, exc_info=True)
    finally:
        cache_db.close()

    # ── generate result ─────────────────────────────────────────────
    from app.services.file_service import build_signed_download_url

    url_info = build_signed_download_url(file_id)

    return {
        "file_id": file_id,
        "file_name": original_name,
        "preview_url": url_info.url,
        "entity_counts": entity_counts,
        "total_entities": total_entities,
        "layers": layers,
        "layer_colors": {k: v for k, v in layer_colors.items()},
        "bounds": bounds,
        "cached": False,
    }


def _preview_error(code: str, message: str) -> Exception:
    from app.core.exceptions import AppHTTPException

    return AppHTTPException(415, code, message)
