"""Render paired, like-for-like DXF previews for human visual inspection.

Preview images are output artifacts only.  They deliberately sit outside the
BH compiler and never influence evidence, proof disposition, or manufacturing
fingerprints.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import nextafter
import os
from pathlib import Path
import warnings

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import Configuration
from ezdxf.fonts import fonts

import matplotlib

matplotlib.use("Agg", force=True)
from matplotlib import pyplot as plt
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from PIL import Image

from .dxf_io import decode_cad_text_transport
from .bh_region import region_boundary


PREVIEW_SCHEMA = "BH-DXF-PREVIEW-1.0"
# Keep the 10:7 review aspect ratio while staying well below common image
# viewers' megapixel and decoded-memory limits. At 400 DPI the 15 x 10.5 inch
# canvas remains substantially sharper than a 4K display without creating the
# 81.6 MP / 311 MiB RGBA raster used by the former 10800 x 7560 setting.
PREVIEW_DPI = 400
PREVIEW_CANVAS_PIXELS = (6000, 4200)
PREVIEW_MAX_PIXELS = 30_000_000
PREVIEW_MARGIN_RATIO = 0.05
_CJK_FONT_CANDIDATES = (
    "NotoSansCJK-Regular.ttc",
    "NotoSansCJKsc-Regular.otf",
    "SourceHanSansCN-Regular.otf",
)
_WINDOWS_CJK_FONT_CANDIDATES = ("simsun.ttc", "msyh.ttc", "simhei.ttf")
_MATPLOTLIB_CJK_FAMILIES = (
    "Noto Sans CJK SC",
    "Source Han Sans CN",
    "DejaVu Sans",
)
_PREVIEW_CODEPAGE_OVERRIDES = {
    # Tekla R12 exports declare GB2312 but ezdxf's automatic R12 detection
    # falls back to cp1252. GBK is a backwards-compatible Python decoder.
    "GB2312": "gbk",
}
_CJK_PREVIEW_STYLE = "BH_CJK_PREVIEW"
_PREVIEW_COLOR_OVERRIDES = {
    "#ffffff": "#000000",
    "#00ffff": "#00008b",
}


@dataclass(frozen=True, slots=True)
class PreviewPair:
    """Two PNG paths rendered with exactly one shared drawing view."""

    before_path: Path
    after_path: Path
    view_bounds: tuple[float, float, float, float]
    canvas_pixels: tuple[int, int]
    dpi: int
    font_fallback: str
    shared_view: bool = True

    def to_report_dict(self) -> dict[str, object]:
        return {
            "before": str(self.before_path.resolve()),
            "after": str(self.after_path.resolve()),
            "shared_view": self.shared_view,
            "view_bounds": list(self.view_bounds),
            "canvas_pixels": list(self.canvas_pixels),
            "dpi": self.dpi,
            "font_fallback": self.font_fallback,
            "renderer": "ezdxf.addons.drawing.matplotlib",
            "schema": PREVIEW_SCHEMA,
        }


class PreviewRenderContext(RenderContext):
    """Increase contrast for light DXF colors on the white review canvas."""

    def resolve_all(self, entity):  # type: ignore[no-untyped-def]
        properties = super().resolve_all(entity)
        properties.color = _PREVIEW_COLOR_OVERRIDES.get(
            properties.color.lower(),
            properties.color,
        )
        return properties


def select_cjk_fallback_font() -> str:
    """Return an installed host CJK font or reject unreadable preview output."""

    manager = fonts.font_manager
    candidates = (
        (*_CJK_FONT_CANDIDATES, *_WINDOWS_CJK_FONT_CANDIDATES)
        if os.name == "nt"
        else _CJK_FONT_CANDIDATES
    )
    for candidate in candidates:
        if manager.has_font(candidate):
            return candidate
    raise RuntimeError(
        "DXF preview requires an installed CJK font: " + ", ".join(candidates)
    )


def _configure_font_fallback() -> str:
    fallback = select_cjk_fallback_font()
    # ezdxf exposes fallback selection as a read-only method; its font manager
    # intentionally stores the resolved filename in this cache field.
    fonts.font_manager._fallback_font_name = fallback
    host_families = (
        ["SimSun", "Microsoft YaHei", *_MATPLOTLIB_CJK_FAMILIES]
        if os.name == "nt"
        else list(_MATPLOTLIB_CJK_FAMILIES)
    )
    plt.rcParams["font.sans-serif"] = host_families
    plt.rcParams["axes.unicode_minus"] = False
    return fallback


def _read_preview_document(path: Path) -> tuple[ezdxf.document.Drawing, str, str]:
    """Read a DXF for display, honoring declared legacy Chinese codepages."""

    detected = ezdxf.readfile(path)
    source_codepage = str(detected.header.get("$DWGCODEPAGE", "")).upper()
    override = _PREVIEW_CODEPAGE_OVERRIDES.get(source_codepage)
    if override is None:
        _decode_preview_text_transport(detected)
        _add_region_preview_proxies(detected)
        return detected, source_codepage, detected.encoding
    document = ezdxf.readfile(path, encoding=override)
    _decode_preview_text_transport(document)
    _add_region_preview_proxies(document)
    return document, source_codepage, override


def _add_region_preview_proxies(document: ezdxf.document.Drawing) -> int:
    """Add non-persisted polyline proxies for the unsupported ACIS renderer."""

    modelspace = document.modelspace()
    added = 0
    for entity in list(modelspace.query("REGION")):
        try:
            boundary = region_boundary(entity)
        except (ValueError, TypeError, IndexError):
            continue
        modelspace.add_lwpolyline(
            boundary.vertices,
            close=True,
            dxfattribs={"layer": entity.dxf.layer},
        )
        added += 1
    return added


def _iter_preview_text_entities(document: ezdxf.document.Drawing):  # type: ignore[no-untyped-def]
    """Yield block text plus INSERT attributes exactly once."""

    for block in document.blocks:
        for entity in block:
            if entity.dxftype() in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                yield entity
            if entity.dxftype() == "INSERT":
                yield from entity.attribs


def _preview_entity_text(entity) -> str:  # type: ignore[no-untyped-def]
    return entity.text if entity.dxftype() == "MTEXT" else entity.dxf.text


def _set_preview_entity_text(entity, value: str) -> None:  # type: ignore[no-untyped-def]
    if entity.dxftype() == "MTEXT":
        entity.text = value
    else:
        entity.dxf.text = value


def _decode_preview_text_transport(document: ezdxf.document.Drawing) -> int:
    """Decode MIF/Unicode transport text in the non-authoritative preview copy."""

    changed = 0
    for entity in _iter_preview_text_entities(document):
        original = _preview_entity_text(entity)
        decoded = decode_cad_text_transport(original)
        if decoded == original:
            continue
        _set_preview_entity_text(entity, decoded)
        changed += 1
    return changed


def _contains_cjk(text: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in text
    )


def _apply_cjk_preview_style(
    document: ezdxf.document.Drawing,
    font_fallback: str,
) -> int:
    """Bind decoded Chinese text to a real CJK font in the preview copy."""

    if _CJK_PREVIEW_STYLE not in document.styles:
        document.styles.add(_CJK_PREVIEW_STYLE, font=font_fallback)
    changed = 0
    for entity in _iter_preview_text_entities(document):
        if _contains_cjk(_preview_entity_text(entity)):
            entity.dxf.style = _CJK_PREVIEW_STYLE
            changed += 1
    return changed


def _modelspace_bounds(path: Path) -> tuple[float, float, float, float]:
    document, _, _ = _read_preview_document(path)
    bounds = bbox.extents(document.modelspace())
    if not bounds.has_data:
        raise ValueError(f"DXF preview requires visible modelspace entities: {path}")
    return (
        float(bounds.extmin.x),
        float(bounds.extmin.y),
        float(bounds.extmax.x),
        float(bounds.extmax.y),
    )


def _shared_view_bounds(
    before_dxf: Path,
    after_dxf: Path,
) -> tuple[float, float, float, float]:
    before = _modelspace_bounds(before_dxf)
    after = _modelspace_bounds(after_dxf)
    min_x = min(before[0], after[0])
    min_y = min(before[1], after[1])
    max_x = max(before[2], after[2])
    max_y = max(before[3], after[3])
    width = max_x - min_x
    height = max_y - min_y
    span = max(width, height, 1.0)
    margin = span * PREVIEW_MARGIN_RATIO
    return (min_x - margin, min_y - margin, max_x + margin, max_y + margin)


def _render_dxf(
    input_dxf: Path,
    output_png: Path,
    *,
    view_bounds: tuple[float, float, float, float],
    title: str,
    font_fallback: str,
) -> None:
    document, _, _ = _read_preview_document(input_dxf)
    _apply_cjk_preview_style(document, font_fallback)
    width_px, height_px = PREVIEW_CANVAS_PIXELS
    pixel_count = width_px * height_px
    if pixel_count > PREVIEW_MAX_PIXELS:
        raise ValueError(
            "DXF preview canvas exceeds the safe decode budget: "
            f"{width_px}x{height_px} ({pixel_count:,} pixels) > "
            f"{PREVIEW_MAX_PIXELS:,} pixels"
        )
    # Agg truncates a binary floating-point pixel product. Move each
    # dimension to the next representable value so an exact target cannot
    # become one pixel smaller in the encoded PNG.
    figure, axis = plt.subplots(
        figsize=(
            nextafter(width_px / PREVIEW_DPI, float("inf")),
            nextafter(height_px / PREVIEW_DPI, float("inf")),
        ),
        dpi=PREVIEW_DPI,
    )
    try:
        context = PreviewRenderContext(document)
        backend = MatplotlibBackend(axis, adjust_figure=False)
        Frontend(
            context,
            backend,
            config=Configuration(custom_bg_color="#ffffff"),
        ).draw_layout(document.modelspace(), finalize=True)
        min_x, min_y, max_x, max_y = view_bounds
        axis.set_xlim(min_x, max_x)
        axis.set_ylim(min_y, max_y)
        axis.set_aspect("equal", adjustable="box")
        axis.set_title(title, fontsize=10)
        axis.set_axis_off()
        output_png.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(
            output_png,
            format="png",
            dpi=PREVIEW_DPI,
            facecolor="white",
            bbox_inches=None,
        )
    finally:
        plt.close(figure)


def _assert_decodeable_pair(before_path: Path, after_path: Path) -> None:
    dimensions: list[tuple[int, int]] = []
    for path in (before_path, after_path):
        if not path.is_file() or path.suffix.lower() != ".png":
            raise ValueError(f"DXF preview file is missing: {path}")
        with warnings.catch_warnings():
            # Treat Pillow's oversized-image warning as a validation failure;
            # preview artifacts must remain safe for ordinary desktop viewers.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
                dimensions.append(image.size)
    if dimensions[0] != PREVIEW_CANVAS_PIXELS:
        raise ValueError(
            f"DXF preview canvas is not {PREVIEW_CANVAS_PIXELS}: {dimensions[0]}"
        )
    if dimensions[0] != dimensions[1]:
        raise ValueError("before/after DXF previews have different canvas dimensions")


def render_preview_pair(
    before_dxf: Path,
    after_dxf: Path,
    preview_root: Path,
    *,
    stem: str,
) -> PreviewPair:
    """Render one before/after PNG pair from the same world-coordinate view."""

    before_dxf = Path(before_dxf)
    after_dxf = Path(after_dxf)
    preview_root = Path(preview_root)
    font_fallback = _configure_font_fallback()
    view_bounds = _shared_view_bounds(before_dxf, after_dxf)
    before_path = preview_root / "before" / f"{stem}_拆板前.png"
    after_path = preview_root / "after" / f"{stem}_拆板后.png"
    _render_dxf(
        before_dxf,
        before_path,
        view_bounds=view_bounds,
        title=f"Before split | {stem} | Font: {font_fallback}",
        font_fallback=font_fallback,
    )
    _render_dxf(
        after_dxf,
        after_path,
        view_bounds=view_bounds,
        title=f"After split | {stem} | Font: {font_fallback}",
        font_fallback=font_fallback,
    )
    _assert_decodeable_pair(before_path, after_path)
    return PreviewPair(
        before_path=before_path,
        after_path=after_path,
        view_bounds=view_bounds,
        canvas_pixels=PREVIEW_CANVAS_PIXELS,
        dpi=PREVIEW_DPI,
        font_fallback=font_fallback,
    )


def validate_preview_pair(
    payload: object,
    *,
    root: Path,
) -> bool:
    """Validate report preview paths are complete, rooted and readable."""

    if not isinstance(payload, dict) or payload.get("shared_view") is not True:
        return False
    before_value = payload.get("before")
    after_value = payload.get("after")
    if not isinstance(before_value, str) or not isinstance(after_value, str):
        return False
    try:
        root_resolved = root.resolve()
        paths = [Path(before_value).resolve(), Path(after_value).resolve()]
        for path in paths:
            path.relative_to(root_resolved)
        _assert_decodeable_pair(paths[0], paths[1])
    except (OSError, ValueError, Image.DecompressionBombWarning):
        return False
    return (
        payload.get("schema") == PREVIEW_SCHEMA
        and payload.get("canvas_pixels") == list(PREVIEW_CANVAS_PIXELS)
        and payload.get("dpi") == PREVIEW_DPI
        and isinstance(payload.get("font_fallback"), str)
        and bool(payload.get("font_fallback"))
        and payload.get("renderer") == "ezdxf.addons.drawing.matplotlib"
    )
