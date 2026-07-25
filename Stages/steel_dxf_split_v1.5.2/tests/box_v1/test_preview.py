from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest
from PIL import Image

from steel_dxf_split.box import preview
from steel_dxf_split.box.box_region import add_contour_region
from steel_dxf_split.box.manufacturing_ir import (
    EvidenceState,
    FeatureEvidence,
    rectangle_contour,
)

EVIDENCE = FeatureEvidence(
    EvidenceState.DIRECT,
    ("source:preview-test",),
    ("BOX.RULE.PREVIEW.TEST",),
    ("BOX.PROOF.PREVIEW.TEST",),
)


def _has_required_cjk_font() -> bool:
    try:
        preview.select_cjk_fallback_font()
    except RuntimeError:
        return False
    return True


def test_preview_copy_adds_renderable_boundary_for_region(tmp_path: Path) -> None:
    source = tmp_path / "region.dxf"
    document = ezdxf.new("R2007")
    add_contour_region(
        document,
        rectangle_contour(0.0, 0.0, 100.0, 50.0, EVIDENCE),
        layer="PLATE_CUT",
    )
    document.saveas(source)

    preview_document, _, _ = preview._read_preview_document(source)

    assert len(preview_document.modelspace().query("REGION")) == 1
    assert len(
        preview_document.modelspace().query("LWPOLYLINE[layer=='PLATE_CUT']")
    ) == 1
    assert not ezdxf.readfile(source).modelspace().query("LWPOLYLINE")


def _drawing(path: Path, text: str) -> None:
    document = ezdxf.new("R2007")
    document.modelspace().add_lwpolyline(
        [(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)],
        close=True,
    )
    document.modelspace().add_text(text, height=10.0)
    document.saveas(path)


@pytest.mark.skipif(
    not _has_required_cjk_font(),
    reason="当前测试环境未安装项目 2 指定的 Noto/思源中文字体",
)
def test_preview_binds_decoded_chinese_text_to_installed_cjk_font(
    tmp_path: Path,
) -> None:
    path = tmp_path / "text.dxf"
    _drawing(path, "腹板")
    document, _, _ = preview._read_preview_document(path)
    fallback = preview.select_cjk_fallback_font()

    changed = preview._apply_cjk_preview_style(document, fallback)
    text = next(iter(document.modelspace().query("TEXT")))

    assert changed == 1
    assert text.dxf.style == "BOX_CJK_PREVIEW"
    assert document.styles.get("BOX_CJK_PREVIEW").dxf.font == fallback


@pytest.mark.skipif(
    not _has_required_cjk_font(),
    reason="当前测试环境未安装项目 2 指定的 Noto/思源中文字体",
)
def test_preview_pair_uses_one_view_and_exact_safe_canvas(
    tmp_path: Path,
    monkeypatch,
) -> None:
    before = tmp_path / "before.dxf"
    after = tmp_path / "after.dxf"
    _drawing(before, "拆板前")
    _drawing(after, "拆板后")
    monkeypatch.setattr(preview, "PREVIEW_DPI", 40)
    monkeypatch.setattr(preview, "PREVIEW_CANVAS_PIXELS", (600, 420))
    monkeypatch.setattr(preview, "PREVIEW_MAX_PIXELS", 300_000)

    pair = preview.render_preview_pair(
        before,
        after,
        tmp_path / "previews",
        stem="TEST",
    )

    assert pair.shared_view
    assert pair.font_fallback
    assert pair.canvas_pixels == (600, 420)
    with Image.open(pair.before_path) as image:
        assert image.size == (600, 420)
    with Image.open(pair.after_path) as image:
        assert image.size == (600, 420)
