from __future__ import annotations

from pathlib import Path
import json

import ezdxf
import pytest

from remnant_drawing_reader import ParseError, parse_dxf
from remnant_drawing_reader.text import normalize_text


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
    assert result.parser_version == "0.2.0"
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


def test_gbk_mif_and_block_attributes_are_normalized_and_parsed(tmp_path: Path) -> None:
    assert normalize_text(r"\M+5B2C4\M+5C1CF: Q235B") == "材料: Q235B"
    document = ezdxf.new("R2018")
    block = document.blocks.new("TITLE_ATTR")
    block.add_attdef("MATERIAL", (0, 0))
    insert = document.modelspace().add_blockref("TITLE_ATTR", (5, 6))
    insert.add_attrib("MATERIAL", r"\M+5B2C4\M+5C1CF: Q355B", (5, 6))
    source = tmp_path / "attrib.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [item.value for item in result.material_candidates] == ["Q355B"]
    assert result.material_candidates[0].evidence[0].entity_type == "ATTRIB"


def test_unrecognized_label_and_encoding_anomaly_emit_stable_warnings(tmp_path: Path) -> None:
    document = ezdxf.new("R2018")
    modelspace = document.modelspace()
    modelspace.add_text("未知字段: ABC").set_placement((0, 0))
    modelspace.add_text("备注: �").set_placement((0, 10))
    source = tmp_path / "warnings.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert {warning.code for warning in result.warnings} == {
        "ENCODING_ANOMALY",
        "UNRECOGNIZED_LABEL",
    }


def test_unlabelled_chinese_text_becomes_project_candidate(tmp_path: Path) -> None:
    document = ezdxf.new("R2018")
    document.modelspace().add_text("这是一段普通备注").set_placement((0, 0))
    source = tmp_path / "plain-text.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.project_candidates] == ["这是一段普通备注"]
    assert result.warnings == []


def test_r2013_ansi_936_header_keeps_utf8_chinese_text(tmp_path: Path) -> None:
    document = ezdxf.new("R2013")
    document.header["$DWGCODEPAGE"] = "ANSI_936"
    document.modelspace().add_text("南京北站016计划").set_placement((0, 0))
    source = tmp_path / "modern-ansi-936-header.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.project_candidates] == ["南京北站016计划"]
    evidence = result.project_candidates[0].evidence[0]
    assert evidence.raw_text == "南京北站016计划"
    assert evidence.normalized_text == "南京北站016计划"
    assert not any(warning.code in {"ENCODING_ANOMALY", "STRUCTURE_ANOMALY"} for warning in result.warnings)


def test_classifies_unlabelled_production_drawing_values(tmp_path: Path) -> None:
    document = ezdxf.new("R2013")
    modelspace = document.modelspace()
    modelspace.add_text("NJB-99-1").set_placement((0, 20))
    modelspace.add_text("南京北站016计划桁架箱型梁火焰零件2026/7/6").set_placement((0, 10))
    modelspace.add_text("Q355B").set_placement((0, 0))
    source = tmp_path / "production-values.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.material_candidates] == ["Q355B"]
    assert [candidate.value for candidate in result.project_candidates] == [
        "南京北站016计划桁架箱型梁火焰零件2026/7/6"
    ]
    assert [candidate.value for candidate in result.part_candidates] == ["NJB-99-1"]
    assert result.warnings == []


def test_unlabelled_project_candidate_keeps_complete_drawing_title(tmp_path: Path) -> None:
    title = "北工大定位板及南京北站017计划天窗2批激光零件 2026-7-03"
    source = tmp_path / "arbitrary-file-name.dxf"
    document = ezdxf.new("R2013")
    document.modelspace().add_text(title).set_placement((0, 0))
    document.saveas(source)

    result = parse_dxf(source)

    assert [item.value for item in result.project_candidates] == [title]


def test_plain_filename_is_never_used_as_project_candidate(tmp_path: Path) -> None:
    source = tmp_path / "南京北站999计划.dxf"
    document = ezdxf.new("R2013")
    document.modelspace().add_text("Q355B").set_placement((0, 0))
    document.saveas(source)

    assert parse_dxf(source).project_candidates == []


def test_multiple_complete_titles_emit_project_conflict(tmp_path: Path) -> None:
    titles = ["南京北站016计划桁架零件", "南京北站017计划天窗零件"]
    source = tmp_path / "conflicting-titles.dxf"
    document = ezdxf.new("R2013")
    for index, title in enumerate(titles):
        document.modelspace().add_text(title).set_placement((0, index * 10))
    document.saveas(source)

    result = parse_dxf(source)

    assert [item.value for item in result.project_candidates] == titles
    assert "PROJECT_CANDIDATES_CONFLICT" in [warning.code for warning in result.warnings]


def test_oversized_project_title_is_not_persistable_candidate(tmp_path: Path) -> None:
    title = "南京北站001计划" + "超" * 128
    source = tmp_path / "oversized-title.dxf"
    document = ezdxf.new("R2013")
    document.modelspace().add_text(title).set_placement((0, 0))
    document.saveas(source)

    result = parse_dxf(source)

    assert result.project_candidates == []
    assert "PROJECT_TITLE_TOO_LONG" in [warning.code for warning in result.warnings]


def test_project_title_at_128_character_boundary_is_accepted(tmp_path: Path) -> None:
    prefix = "南京北站001计划"
    title = prefix + "边" * (128 - len(prefix))
    source = tmp_path / "boundary-title.dxf"
    document = ezdxf.new("R2013")
    document.modelspace().add_text(title).set_placement((0, 0))
    document.saveas(source)

    result = parse_dxf(source)

    assert [item.value for item in result.project_candidates] == [title]
    assert "PROJECT_TITLE_TOO_LONG" not in [warning.code for warning in result.warnings]


@pytest.mark.parametrize(
    "metadata",
    ["REV-2026-07", "DATE-2026-07", "ISO-9001-2015", "REV-123-1", "DWG-99-1"],
)
def test_unlabelled_hyphenated_metadata_is_not_a_part_number(
    tmp_path: Path, metadata: str
) -> None:
    document = ezdxf.new("R2013")
    document.modelspace().add_text(metadata).set_placement((0, 0))
    source = tmp_path / "metadata.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert result.part_candidates == []
    assert result.warnings == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Q345GJC", "Q345GJC"),
        ("(Q345GJC-Z15)", "Q345GJC-Z15"),
        ("Q390GJC-Z15", "Q390GJC-Z15"),
        ("Q460GJB-Z25", "Q460GJB-Z25"),
        ("(Q420GJC-Z25)", "Q420GJC-Z25"),
    ],
)
def test_extracts_expanded_unlabelled_material_grades(
    tmp_path: Path, raw: str, expected: str
) -> None:
    document = ezdxf.new("R2018")
    document.modelspace().add_text(raw).set_placement((0, 0))
    source = tmp_path / "material.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.material_candidates] == [expected]
    assert result.warnings == []


def test_extracts_material_and_part_from_composite_text(tmp_path: Path) -> None:
    document = ezdxf.new("R2018")
    document.modelspace().add_text("Q345GJC-Z15 JWL-36-01").set_placement((0, 0))
    source = tmp_path / "composite.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.material_candidates] == ["Q345GJC-Z15"]
    assert [candidate.value for candidate in result.part_candidates] == ["JWL-36-01"]
    assert result.warnings == []


def test_extracts_structurally_similar_part_numbers_in_first_seen_order(
    tmp_path: Path,
) -> None:
    values = [
        "JWL-1014-B-4",
        "ND-1053-3",
        "DS-481-4",
        "SZKJ-07-2",
        "AYWT-6-1",
        "YM-42-2",
        "YL42-2",
        "YZ-18-1",
        "LYTL-05",
        "3CB-3D-1",
    ]
    document = ezdxf.new("R2018")
    for index, value in enumerate(values):
        document.modelspace().add_text(value).set_placement((0, index * 10))
    source = tmp_path / "parts.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.part_candidates] == values
    assert result.warnings == []


@pytest.mark.parametrize("annotation", ["余料", "未到料", "返修件"])
def test_ignores_standalone_two_or_three_chinese_character_annotations(
    tmp_path: Path, annotation: str
) -> None:
    document = ezdxf.new("R2018")
    document.modelspace().add_text(annotation).set_placement((0, 0))
    source = tmp_path / "short-annotation.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert result.material_candidates == []
    assert result.project_candidates == []
    assert result.part_candidates == []
    assert result.warnings == []


def test_all_longer_chinese_texts_become_project_candidates(tmp_path: Path) -> None:
    titles = [
        "精武路46-47核心筒阚零件2022-3-22",
        "外框F62~F63层主板2022-12-01下发",
        "精武路三层梁主板余料11.16",
    ]
    document = ezdxf.new("R2018")
    for index, title in enumerate(titles):
        document.modelspace().add_text(title).set_placement((0, index * 10))
    source = tmp_path / "projects.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.project_candidates] == titles
    assert [warning.code for warning in result.warnings] == ["PROJECT_CANDIDATES_CONFLICT"]


def test_ignores_unclassified_dimensions_dates_and_plain_ascii(tmp_path: Path) -> None:
    document = ezdxf.new("R2018")
    for index, text in enumerate(["630", "2022-8-15", "PLAIN NOTE"]):
        document.modelspace().add_text(text).set_placement((0, index * 10))
    source = tmp_path / "ordinary-text.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert result.material_candidates == []
    assert result.project_candidates == []
    assert result.part_candidates == []
    assert result.warnings == []


def test_normal_non_text_geometry_does_not_emit_structure_warning(tmp_path: Path) -> None:
    document = ezdxf.new("R2013")
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (10, 0), (10, 10)], close=True)
    modelspace.add_line((0, 0), (10, 10))
    modelspace.add_text("Q355B").set_placement((0, 0))
    source = tmp_path / "geometry-and-text.dxf"
    document.saveas(source)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.material_candidates] == ["Q355B"]
    assert not any(warning.code == "STRUCTURE_ANOMALY" for warning in result.warnings)


def test_single_malformed_entity_emits_structure_warning_and_keeps_other_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    from remnant_drawing_reader import reader

    document = ezdxf.new("R2018")
    document.modelspace().add_text("材质: Q235B").set_placement((0, 10))
    document.modelspace().add_text("BROKEN").set_placement((0, 0))
    source = tmp_path / "recoverable-structure.dxf"
    document.saveas(source)
    original = reader._evidence

    def flaky_evidence(entity, block_path):
        if entity.dxftype() == "TEXT" and str(entity.dxf.text) == "BROKEN":
            raise ValueError("malformed entity")
        return original(entity, block_path)

    monkeypatch.setattr(reader, "_evidence", flaky_evidence)

    result = parse_dxf(source)

    assert [candidate.value for candidate in result.material_candidates] == ["Q235B"]
    assert [warning.code for warning in result.warnings] == ["STRUCTURE_ANOMALY"]
