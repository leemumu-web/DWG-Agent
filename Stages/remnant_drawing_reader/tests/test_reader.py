from __future__ import annotations

from pathlib import Path
import json

import ezdxf
import pytest

from remnant_drawing_reader import ParseError, parse_dxf


def _save_labelled_drawing(path: Path) -> None:
    document = ezdxf.new("R2018")
    modelspace = document.modelspace()
    modelspace.add_text("材质: Q235B-Z15", dxfattribs={"layer": "TITLE"}).set_placement((10, 30))
    modelspace.add_mtext("项目编号: PJ-2026-001", dxfattribs={"layer": "TITLE"}).set_location((10, 20))
    modelspace.add_text("零件编号: L-101、L-102", dxfattribs={"layer": "PARTS"}).set_placement((10, 10))
    document.saveas(path)


def _save_nested_drawing(path: Path) -> None:
    document = ezdxf.new("R2018")
    inner = document.blocks.new("PARTS")
    inner.add_text("零件编号: L-101、L-102", dxfattribs={"layer": "PARTS"}).set_placement((0, 0))
    outer = document.blocks.new("TITLE")
    outer.add_blockref("PARTS", (2, 3))
    document.modelspace().add_blockref("TITLE", (10, 20))
    document.saveas(path)


def test_extracts_labelled_candidates_and_preserves_material_suffix(tmp_path: Path) -> None:
    source = tmp_path / "labelled.dxf"
    _save_labelled_drawing(source)

    result = parse_dxf(source)

    assert result.schema_version == "1.0"
    assert result.parser_version == "0.1.0"
    assert len(result.source_sha256) == 64
    assert [candidate.value for candidate in result.material_candidates] == ["Q235B-Z15"]
    assert [candidate.value for candidate in result.project_candidates] == ["PJ-2026-001"]
    assert [candidate.value for candidate in result.part_candidates] == ["L-101", "L-102"]
    assert result.warnings == []


def test_nested_insert_preserves_block_path_and_world_position(tmp_path: Path) -> None:
    source = tmp_path / "nested.dxf"
    _save_nested_drawing(source)

    result = parse_dxf(source)

    evidence = result.part_candidates[0].evidence[0]
    assert evidence.block_path == ["TITLE", "PARTS"]
    assert evidence.layer == "PARTS"
    assert evidence.entity_type == "TEXT"
    assert evidence.x == pytest.approx(12.0)
    assert evidence.y == pytest.approx(23.0)
    assert evidence.handle


def test_duplicate_part_numbers_are_deduplicated_in_first_seen_order(tmp_path: Path) -> None:
    document = ezdxf.new("R2018")
    modelspace = document.modelspace()
    modelspace.add_text("零件编号: L-102, L-101", dxfattribs={"layer": "PARTS"}).set_placement((0, 10))
    modelspace.add_text("零件号: L-101", dxfattribs={"layer": "PARTS"}).set_placement((0, 0))
    source = tmp_path / "duplicates.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.part_candidates] == ["L-102", "L-101"]
    assert len(result.part_candidates[1].evidence) == 2


def test_conflicting_single_value_candidates_emit_warnings(tmp_path: Path) -> None:
    document = ezdxf.new("R2018")
    modelspace = document.modelspace()
    modelspace.add_text("材质: Q235B").set_placement((0, 10))
    modelspace.add_text("材质: Q235D").set_placement((0, 0))
    source = tmp_path / "conflict.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.material_candidates] == ["Q235B", "Q235D"]
    assert [warning.code for warning in result.warnings] == ["MATERIAL_CANDIDATES_CONFLICT"]


def test_unreadable_dxf_raises_stable_error_without_host_path(tmp_path: Path) -> None:
    source = tmp_path / "secret-business-name.dxf"
    source.write_bytes(b"not a dxf")

    with pytest.raises(ParseError) as captured:
        parse_dxf(source)

    assert str(captured.value) == "REMNANT_DXF_UNREADABLE"
    assert str(tmp_path) not in str(captured.value)


def test_cli_writes_utf8_versioned_json(tmp_path: Path) -> None:
    from remnant_drawing_reader.cli import main

    source = tmp_path / "labelled.dxf"
    output = tmp_path / "result.json"
    _save_labelled_drawing(source)

    assert main([str(source), "--output", str(output)]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["material_candidates"][0]["value"] == "Q235B-Z15"
