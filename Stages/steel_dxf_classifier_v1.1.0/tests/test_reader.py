from pathlib import Path

import ezdxf
import pytest

from steel_dxf_classifier.reader import DXFReadError, read_text_facts
from steel_dxf_classifier.text import normalize_text


def _save_title_block(path: Path) -> None:
    doc = ezdxf.new("R2010")
    block = doc.blocks.new("TITLE")
    block.add_text("截面", dxfattribs={"insert": (0, 10), "height": 2})
    block.add_attdef("PROFILE", insert=(0, 0), text="", dxfattribs={"height": 2})
    insert = doc.modelspace().add_blockref("TITLE", (100, 200))
    insert.add_auto_attribs({"PROFILE": "BH300*200*6*8"})
    doc.saveas(path)


def test_reader_expands_insert_text_and_attributes(tmp_path: Path) -> None:
    path = tmp_path / "part.dxf"
    _save_title_block(path)

    facts, metadata = read_text_facts(path)

    by_text = {fact.normalized: fact for fact in facts}
    assert {"截面", "BH300*200*6*8"} <= by_text.keys()
    assert by_text["截面"].x == pytest.approx(100.0)
    assert by_text["截面"].y == pytest.approx(210.0)
    assert by_text["BH300*200*6*8"].entity_type == "ATTRIB"
    assert by_text["截面"].block_path == ("TITLE",)
    assert metadata["source_codepage"]
    assert metadata["text_fact_count"] == len(facts)


def test_reader_expands_nested_insert_coordinates(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    leaf = doc.blocks.new("LEAF")
    leaf.add_text("PL20*300", dxfattribs={"insert": (1, 2), "height": 1})
    outer = doc.blocks.new("OUTER")
    outer.add_blockref("LEAF", (10, 20))
    doc.modelspace().add_blockref("OUTER", (100, 200))
    path = tmp_path / "nested.dxf"
    doc.saveas(path)

    facts, _ = read_text_facts(path)
    profile = next(fact for fact in facts if fact.normalized == "PL20*300")

    assert profile.x == pytest.approx(111.0)
    assert profile.y == pytest.approx(222.0)
    assert profile.block_path == ("OUTER", "LEAF")


def test_reader_rejects_corrupted_dxf(tmp_path: Path) -> None:
    path = tmp_path / "broken.dxf"
    path.write_text("not a dxf", encoding="utf-8")

    with pytest.raises(DXFReadError, match="broken.dxf"):
        read_text_facts(path)


def test_normalizer_decodes_legacy_gbk_mif_sequences() -> None:
    assert normalize_text(r"\M+5BDD8\M+5C3E6\M+5D0CD\M+5B2C4") == "截面型材"
    assert normalize_text(r"\U+622A\U+9762") == "截面"
