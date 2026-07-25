from __future__ import annotations

import os
from pathlib import Path

import ezdxf
import pytest
from ezdxf.addons.drawing import RenderContext
from PIL import Image

from steel_dxf_split import dxf_preview
from steel_dxf_split.dxf_preview import (
    PREVIEW_CANVAS_PIXELS,
    PREVIEW_DPI,
    PREVIEW_MAX_PIXELS,
    PreviewRenderContext,
    _CJK_FONT_CANDIDATES,
    _MATPLOTLIB_CJK_FAMILIES,
    _apply_cjk_preview_style,
    _decode_preview_text_transport,
    _read_preview_document,
    render_preview_pair,
    select_cjk_fallback_font,
)
from steel_dxf_split.bh_models import BulgeContour, BulgeVertex
from steel_dxf_split.bh_region import add_contour_region


ROOT = Path(__file__).resolve().parents[2]


def test_preview_copy_adds_renderable_boundary_for_region(tmp_path: Path) -> None:
    from steel_dxf_split.dxf_preview import _read_preview_document

    source = tmp_path / "region.dxf"
    document = ezdxf.new("R2007")
    add_contour_region(
        document,
        BulgeContour(
            [
                BulgeVertex(0.0, 0.0),
                BulgeVertex(100.0, 0.0),
                BulgeVertex(100.0, 50.0),
                BulgeVertex(0.0, 50.0),
            ]
        ),
        layer="PLATE_CUT",
    )
    document.saveas(source)

    preview, _, _ = _read_preview_document(source)

    assert len(preview.modelspace().query("REGION")) == 1
    assert len(preview.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")) == 1
GB2312_SOURCE = ROOT / "samples" / "bh_pairs" / "2b1-cb-26_拆板前.dxf"


def _drawing_with_line(path: Path, start: tuple[float, float], end: tuple[float, float]) -> Path:
    document = ezdxf.new("R2010")
    document.modelspace().add_line(start, end)
    document.saveas(path)
    return path


@pytest.mark.skipif(os.name == "nt", reason="Production CJK preview rendering is Linux-only")
def test_render_preview_pair_writes_decodeable_pngs_with_one_shared_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dxf_preview, "PREVIEW_CANVAS_PIXELS", (720, 504))
    before = _drawing_with_line(tmp_path / "before.dxf", (0.0, 0.0), (100.0, 20.0))
    after = _drawing_with_line(tmp_path / "after.dxf", (20.0, 5.0), (80.0, 15.0))

    pair = render_preview_pair(before, after, tmp_path / "previews", stem="demo")

    assert pair.before_path == tmp_path / "previews" / "before" / "demo_拆板前.png"
    assert pair.after_path == tmp_path / "previews" / "after" / "demo_拆板后.png"
    assert pair.before_path.is_file()
    assert pair.after_path.is_file()
    assert pair.shared_view is True
    assert pair.view_bounds[0] < 0.0 < pair.view_bounds[2]
    assert pair.view_bounds[1] < 0.0 < pair.view_bounds[3]
    assert pair.view_bounds[0] < 100.0 < pair.view_bounds[2]
    assert pair.view_bounds[1] < 20.0 < pair.view_bounds[3]
    with Image.open(pair.before_path) as before_image, Image.open(
        pair.after_path
    ) as after_image:
        assert before_image.size == pair.canvas_pixels
        assert after_image.size == pair.canvas_pixels
        assert before_image.size == after_image.size
    assert PREVIEW_CANVAS_PIXELS == (6000, 4200)
    assert PREVIEW_CANVAS_PIXELS[0] * PREVIEW_CANVAS_PIXELS[1] == 25_200_000
    assert PREVIEW_CANVAS_PIXELS[0] * PREVIEW_CANVAS_PIXELS[1] <= PREVIEW_MAX_PIXELS
    assert PREVIEW_DPI == 400
    assert pair.canvas_pixels == (720, 504)
    assert pair.dpi == 400
    assert pair.font_fallback == select_cjk_fallback_font()


@pytest.mark.skipif(os.name == "nt", reason="Production CJK preview rendering is Linux-only")
def test_preview_refuses_a_canvas_above_the_safe_pixel_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dxf_preview, "PREVIEW_CANVAS_PIXELS", (10000, 4000))
    before = _drawing_with_line(tmp_path / "before.dxf", (0.0, 0.0), (1.0, 1.0))
    after = _drawing_with_line(tmp_path / "after.dxf", (0.0, 0.0), (1.0, 1.0))

    with pytest.raises(ValueError, match="safe decode budget"):
        render_preview_pair(before, after, tmp_path / "previews", stem="oversized")


def test_preview_context_maps_white_entities_to_black_on_white_canvas() -> None:
    document = ezdxf.new("R2010")
    white_line = document.modelspace().add_line(
        (0.0, 0.0),
        (10.0, 0.0),
        dxfattribs={"color": 7},
    )

    properties = PreviewRenderContext(document).resolve_all(white_line)

    assert RenderContext(document).resolve_all(white_line).color == "#ffffff"
    assert properties.color == "#000000"


def test_preview_context_maps_cyan_entities_to_dark_blue() -> None:
    document = ezdxf.new("R2010")
    cyan_line = document.modelspace().add_line(
        (0.0, 0.0),
        (10.0, 0.0),
        dxfattribs={"color": 4},
    )

    properties = PreviewRenderContext(document).resolve_all(cyan_line)

    assert RenderContext(document).resolve_all(cyan_line).color == "#00ffff"
    assert properties.color == "#00008b"


def test_preview_reader_honors_declared_gb2312_for_block_text() -> None:
    document, source_codepage, preview_encoding = _read_preview_document(
        GB2312_SOURCE
    )
    texts = [
        entity.dxf.text
        for block in document.blocks
        for entity in block
        if entity.dxftype() == "TEXT"
    ]

    assert source_codepage == "GB2312"
    assert preview_encoding == "gbk"
    assert "编号" in texts
    assert "±àºÅ" not in texts


def test_chinese_text_is_bound_to_available_cjk_font_style() -> None:
    document, _, _ = _read_preview_document(GB2312_SOURCE)

    changed = _apply_cjk_preview_style(document, "NotoSansCJK-Regular.ttc")
    chinese = [
        entity
        for block in document.blocks
        for entity in block
        if entity.dxftype() in {"TEXT", "MTEXT"}
        and "编号" in (entity.dxf.text if entity.dxftype() == "TEXT" else entity.text)
    ]

    assert changed > 0
    assert chinese
    assert all(entity.dxf.style == "BH_CJK_PREVIEW" for entity in chinese)
    assert document.styles.get("BH_CJK_PREVIEW").dxf.font == "NotoSansCJK-Regular.ttc"


def test_preview_decodes_mif_for_nested_text_and_attributes_before_font_binding() -> None:
    document = ezdxf.new("R2000")
    block = document.blocks.new("MIF_MARK")
    nested_text = block.add_text(r"\M+5C1E3\M+5BCFE\M+5B1E0\M+5BAC5")
    nested_mtext = block.add_mtext(r"\M+5CDBC\M+5D6BD 16\M+5A6B522")
    document.modelspace().add_blockref("MIF_MARK", (0.0, 0.0))
    attributed = document.modelspace().add_blockref("MIF_MARK", (100.0, 0.0))
    attribute = attributed.add_attrib("NAME", r"\M+5B9E6\M+5B8F1")

    changed = _decode_preview_text_transport(document)
    styled = _apply_cjk_preview_style(document, "NotoSansCJK-Regular.ttc")

    assert changed == 3
    assert nested_text.dxf.text == "零件编号"
    assert nested_mtext.text == "图纸 16Φ22"
    assert attribute.dxf.text == "规格"
    assert styled == 3
    assert nested_text.dxf.style == "BH_CJK_PREVIEW"
    assert nested_mtext.dxf.style == "BH_CJK_PREVIEW"
    assert attribute.dxf.style == "BH_CJK_PREVIEW"


def test_cjk_preview_font_candidates_are_linux_native() -> None:
    assert "NotoSansCJK-Regular.ttc" in _CJK_FONT_CANDIDATES
    assert "SourceHanSansCN-Regular.otf" in _CJK_FONT_CANDIDATES
    assert "msyh.ttc" not in _CJK_FONT_CANDIDATES
    assert "simhei.ttf" not in _CJK_FONT_CANDIDATES
    assert "Noto Sans CJK SC" in _MATPLOTLIB_CJK_FAMILIES
    assert "Source Han Sans CN" in _MATPLOTLIB_CJK_FAMILIES
    assert "Microsoft YaHei" not in _MATPLOTLIB_CJK_FAMILIES
    assert "SimHei" not in _MATPLOTLIB_CJK_FAMILIES


def test_preview_refuses_to_render_chinese_without_a_linux_cjk_font(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoCjkFonts:
        @staticmethod
        def has_font(_candidate: str) -> bool:
            return False

        @staticmethod
        def fallback_font_name() -> str:
            return "arial.ttf"

    monkeypatch.setattr(dxf_preview.fonts, "font_manager", NoCjkFonts())

    with pytest.raises(RuntimeError, match="installed CJK font"):
        select_cjk_fallback_font()
