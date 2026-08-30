from __future__ import annotations

import sys
from pathlib import Path

import ezdxf
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MERGE_SCRIPT_DIR = PROJECT_ROOT.parent / "太子"
sys.path.insert(0, str(MERGE_SCRIPT_DIR))

import generate_combined_two_column_sheet as merge_sheet  # noqa: E402


def _gb2312_dxf(path: Path) -> Path:
    document = ezdxf.new("R2000")
    document.modelspace().add_text("零件图")
    document.saveas(path, encoding="gbk")
    payload = path.read_bytes().replace(b"ANSI_1252", b"GB2312  ", 1)
    path.write_bytes(payload)
    return path


def test_read_dxf_document_decodes_legacy_gb2312_chinese(tmp_path: Path) -> None:
    path = _gb2312_dxf(tmp_path / "legacy-gb2312.dxf")

    document = merge_sheet.read_dxf_document(path)

    text_entities = [
        entity.dxf.text
        for entity in document.modelspace()
        if entity.dxftype() == "TEXT"
    ]
    assert document.encoding == "gbk"
    assert text_entities == ["零件图"]


def test_copy_visible_entity_does_not_silently_drop_translation_failures() -> None:
    class FailingEntity:
        def dxftype(self) -> str:
            return "LINE"

        def copy(self) -> "FailingEntity":
            return self

        def translate(self, _dx: float, _dy: float, _dz: float) -> None:
            raise RuntimeError("translation failed")

    target = ezdxf.new("R2010").modelspace()

    with pytest.raises(ValueError, match="无法平移实体 LINE"):
        merge_sheet.copy_visible_entity(target, FailingEntity(), 0.0, 0.0)


def test_copy_visible_entity_skips_only_a_proven_empty_insert() -> None:
    source = ezdxf.new("R2010")
    source.blocks.new("EMPTY_BLOCK")
    insert = source.modelspace().add_blockref("EMPTY_BLOCK", (0.0, 0.0))
    target = ezdxf.new("R2010").modelspace()

    assert merge_sheet.copy_visible_entity(target, insert, 0.0, 0.0) == 0
    assert len(target) == 0


def test_part_number_key_prioritizes_cb_numeric_suffix() -> None:
    names = [
        "BYSJ@零件图@a1-10-cb-2",
        "BYSJ@零件图@a1-4-cb-10",
        "BYSJ@零件图@a1-4-cb-1",
    ]

    assert sorted(names, key=merge_sheet.part_number_key) == [
        "BYSJ@零件图@a1-4-cb-1",
        "BYSJ@零件图@a1-10-cb-2",
        "BYSJ@零件图@a1-4-cb-10",
    ]
