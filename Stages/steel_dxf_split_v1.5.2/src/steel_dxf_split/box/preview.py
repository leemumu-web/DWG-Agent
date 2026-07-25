"""Non-authoritative paired DXF previews with explicit Chinese font binding."""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from dataclasses import dataclass
from math import nextafter
from pathlib import Path

import ezdxf
import matplotlib
from ezdxf import bbox
from ezdxf.addons.drawing.config import Configuration
from ezdxf.addons.drawing.frontend import Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.properties import RenderContext
from ezdxf.entities.dxfentity import DXFEntity
from ezdxf.entities.insert import Insert
from ezdxf.entities.mtext import MText
from ezdxf.filemanagement import readfile
from ezdxf.fonts import fonts

matplotlib.use("Agg", force=True)
from matplotlib import pyplot as plt
from PIL import Image

from .box_region import region_boundary
from .dxf_io import decode_cad_text_transport

PREVIEW_SCHEMA = "BOX-DXF-PREVIEW-1.0"
PREVIEW_DPI = 400
PREVIEW_CANVAS_PIXELS = (6000, 4200)
PREVIEW_MAX_PIXELS = 30_000_000
PREVIEW_MARGIN_RATIO = 0.05
_CJK_FONT_CANDIDATES = (
    "NotoSansCJK-Regular.ttc",
    "NotoSansCJKsc-Regular.otf",
    "SourceHanSansCN-Regular.otf",
)
_MATPLOTLIB_CJK_FAMILIES = (
    "Noto Sans CJK SC",
    "Source Han Sans CN",
    "DejaVu Sans",
)
_CODEPAGE_OVERRIDES = {"GB2312": "gbk"}
_CJK_PREVIEW_STYLE = "BOX_CJK_PREVIEW"
_COLOR_OVERRIDES = {"#ffffff": "#000000", "#00ffff": "#00008b"}


@dataclass(frozen=True, slots=True)
class PreviewPair:
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
    """Map Tekla light colors to readable white-background preview colors."""

    def resolve_all(self, entity):  # type: ignore[no-untyped-def]
        properties = super().resolve_all(entity)
        properties.color = _COLOR_OVERRIDES.get(
            properties.color.lower(), properties.color
        )
        return properties


def select_cjk_fallback_font() -> str:
    manager = fonts.font_manager
    for candidate in _CJK_FONT_CANDIDATES:
        if manager.has_font(candidate):
            return candidate
    raise RuntimeError(
        "DXF preview requires an installed CJK font: " + ", ".join(_CJK_FONT_CANDIDATES)
    )


def _configure_font_fallback() -> str:
    fallback = select_cjk_fallback_font()
    # ezdxf intentionally exposes selection but not a public fallback setter.
    fonts.font_manager._fallback_font_name = fallback
    plt.rcParams["font.sans-serif"] = list(_MATPLOTLIB_CJK_FAMILIES)
    plt.rcParams["axes.unicode_minus"] = False
    return fallback


def _iter_preview_text_entities(
    document: ezdxf.document.Drawing,
) -> Iterator[DXFEntity]:
    for block in document.blocks:
        for entity in block:
            if entity.dxftype() in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}:
                yield entity
            if entity.dxftype() == "INSERT":
                assert isinstance(entity, Insert)
                yield from entity.attribs


def _entity_text(entity: DXFEntity) -> str:
    if entity.dxftype() == "MTEXT":
        assert isinstance(entity, MText)
        return str(entity.text)
    return str(entity.dxf.text)


def _set_entity_text(entity: DXFEntity, value: str) -> None:
    if entity.dxftype() == "MTEXT":
        assert isinstance(entity, MText)
        entity.text = value
    else:
        entity.dxf.text = value


def _decode_preview_text_transport(document: ezdxf.document.Drawing) -> int:
    changed = 0
    for entity in _iter_preview_text_entities(document):
        original = _entity_text(entity)
        decoded = decode_cad_text_transport(original)
        if decoded != original:
            _set_entity_text(entity, decoded)
            changed += 1
    return changed


def _read_preview_document(
    path: Path,
) -> tuple[ezdxf.document.Drawing, str, str]:
    detected = readfile(path)
    codepage = str(detected.header.get("$DWGCODEPAGE", "")).upper()
    override = _CODEPAGE_OVERRIDES.get(codepage)
    document = detected if override is None else readfile(path, encoding=override)
    _decode_preview_text_transport(document)
    _add_region_preview_proxies(document)
    return document, codepage, document.encoding if override is None else override


def _add_region_preview_proxies(document: ezdxf.document.Drawing) -> int:
    """Add non-persisted boundaries for the renderer's unsupported REGIONs."""

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


def _contains_cjk(text: str) -> bool:
    return any(
        "\u3400" <= character <= "\u4dbf"
        or "\u4e00" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        for character in text
    )


def _apply_cjk_preview_style(
    document: ezdxf.document.Drawing,
    font_fallback: str,
) -> int:
    if _CJK_PREVIEW_STYLE not in document.styles:
        document.styles.add(_CJK_PREVIEW_STYLE, font=font_fallback)
    changed = 0
    for entity in _iter_preview_text_entities(document):
        if _contains_cjk(_entity_text(entity)):
            entity.dxf.style = _CJK_PREVIEW_STYLE
            changed += 1
    return changed


def _modelspace_bounds(path: Path) -> tuple[float, float, float, float]:
    document, _, _ = _read_preview_document(path)
    extents = bbox.extents(document.modelspace())
    if not extents.has_data:
        raise ValueError(f"DXF preview requires visible modelspace entities: {path}")
    return (
        float(extents.extmin.x),
        float(extents.extmin.y),
        float(extents.extmax.x),
        float(extents.extmax.y),
    )


def _shared_view_bounds(
    before_dxf: Path,
    after_dxf: Path,
) -> tuple[float, float, float, float]:
    before = _modelspace_bounds(before_dxf)
    after = _modelspace_bounds(after_dxf)
    minimum_x = min(before[0], after[0])
    minimum_y = min(before[1], after[1])
    maximum_x = max(before[2], after[2])
    maximum_y = max(before[3], after[3])
    margin = (
        max(maximum_x - minimum_x, maximum_y - minimum_y, 1.0) * PREVIEW_MARGIN_RATIO
    )
    return (
        minimum_x - margin,
        minimum_y - margin,
        maximum_x + margin,
        maximum_y + margin,
    )


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
    pixels = width_px * height_px
    if pixels > PREVIEW_MAX_PIXELS:
        raise ValueError(
            f"DXF preview canvas exceeds safe budget: {width_px}x{height_px}"
        )
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
        minimum_x, minimum_y, maximum_x, maximum_y = view_bounds
        axis.set_xlim(minimum_x, maximum_x)
        axis.set_ylim(minimum_y, maximum_y)
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
        raise ValueError("before/after DXF previews have different dimensions")


def render_preview_pair(
    before_dxf: Path,
    after_dxf: Path,
    preview_root: Path,
    *,
    stem: str,
) -> PreviewPair:
    fallback = _configure_font_fallback()
    bounds = _shared_view_bounds(before_dxf, after_dxf)
    before_path = preview_root / "before" / f"{stem}_拆板前.png"
    after_path = preview_root / "after" / f"{stem}_拆板后.png"
    _render_dxf(
        before_dxf,
        before_path,
        view_bounds=bounds,
        title=f"Before split | {stem} | Font: {fallback}",
        font_fallback=fallback,
    )
    _render_dxf(
        after_dxf,
        after_path,
        view_bounds=bounds,
        title=f"After split | {stem} | Font: {fallback}",
        font_fallback=fallback,
    )
    _assert_decodeable_pair(before_path, after_path)
    return PreviewPair(
        before_path=before_path,
        after_path=after_path,
        view_bounds=bounds,
        canvas_pixels=PREVIEW_CANVAS_PIXELS,
        dpi=PREVIEW_DPI,
        font_fallback=fallback,
    )


def validate_preview_pair(payload: object, *, root: Path) -> bool:
    if not isinstance(payload, dict) or payload.get("shared_view") is not True:
        return False
    before = payload.get("before")
    after = payload.get("after")
    if not isinstance(before, str) or not isinstance(after, str):
        return False
    try:
        root_path = root.resolve()
        paths = (Path(before).resolve(), Path(after).resolve())
        for path in paths:
            path.relative_to(root_path)
        _assert_decodeable_pair(*paths)
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
