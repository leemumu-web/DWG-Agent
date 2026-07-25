from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from steel_dxf_split.box.metadata import (
    MetadataResolutionError,
    parse_box_profile,
    resolve_box_metadata,
)
from steel_dxf_split.box.source_ir import SourceEntityIR, build_source_ir
from tests.box_v1.paths import INPUTS, PROJECT_1_INPUTS, PROJECT_2_INPUTS

PROJECT_1_SAMPLE = PROJECT_1_INPUTS / "w3-cb-57_拆板前.dxf"


EXPECTED = {
    "2b1-cb-56": (1100.0, 1100.0, 60.0, 60.0, 7092.0, "Q420GJC-Z25", 20),
    "2b1-cb-86": (750.0, 850.0, 20.0, 20.0, 1329.0, "Q390B", 10),
    "2b1-cb-91": (780.0, 800.0, 20.0, 20.0, 1825.0, "Q390B", 10),
    "2b1-cb-92": (780.0, 800.0, 20.0, 20.0, 1825.0, "Q390B", 10),
    "2b2-cb-145": (1800.0, 800.0, 40.0, 60.0, 5875.0, "Q390GJC-Z25", 20),
    "2b2-cb-155": (1800.0, 1400.0, 40.0, 80.0, 8192.0, "Q420GJC-Z35", 20),
    "2b2-cb-2": (1500.0, 600.0, 30.0, 50.0, 12755.0, "Q390GJC-Z15", 30),
    "2t1-cb-95": (750.0, 850.0, 20.0, 20.0, 1758.0, "Q390B", 10),
    "3t2-cb-117": (600.0, 600.0, 30.0, 30.0, 5870.0, "Q390B", 20),
    "h-3-cb-2": (1000.0, 1000.0, 45.0, 45.0, 3700.0, "Q390GJC-Z15", 10),
    "h-4-cb-37": (800.0, 300.0, 20.0, 30.0, 14048.0, "Q355B", 40),
    "h-4-cb-38": (800.0, 300.0, 20.0, 30.0, 14048.0, "Q355B", 40),
    "h-9-cb-116": (300.0, 1000.0, 16.0, 16.0, 5824.0, "Q355B", 20),
    "h-9-cb-124": (300.0, 1000.0, 16.0, 16.0, 5816.0, "Q355B", 20),
    "h-9-cb-133": (300.0, 1000.0, 16.0, 16.0, 1697.0, "Q355B", 10),
    "h-9-cb-279": (300.0, 1000.0, 30.0, 40.0, 10023.0, "Q390GJC-Z15", 25),
    "h-9-cb-69": (300.0, 1000.0, 30.0, 40.0, 3458.0, "Q390GJC-Z15", 10),
    "h-9-cb-72": (300.0, 1000.0, 30.0, 40.0, 10023.0, "Q390GJC-Z15", 25),
    "h-9-cb-73": (300.0, 1000.0, 30.0, 40.0, 8281.0, "Q390GJC-Z15", 20),
    "h-9-cb-94": (300.0, 930.0, 16.0, 16.0, 2296.0, "Q355B", 10),
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BOX1100*1100*60*60", (1100.0, 1100.0, 60.0, 60.0)),
        ("box 750 × 850 × 20 × 20", (750.0, 850.0, 20.0, 20.0)),
        ("BOX780x800x20x20", (780.0, 800.0, 20.0, 20.0)),
    ],
)
def test_parse_box_profile_transport_variants(
    raw: str,
    expected: tuple[float, float, float, float],
) -> None:
    profile = parse_box_profile(raw)

    assert (
        profile.height,
        profile.width,
        profile.web_thickness,
        profile.flange_thickness,
    ) == expected
    assert profile.web_clear_width == expected[0] - 2.0 * expected[3]


@pytest.mark.parametrize(
    "raw",
    [
        "BH1100*1100*60*60",
        "BOX1100*1100*60",
        "BOX100*100*60*60",
        "BOX100*100*10*60",
        "BOX0*100*10*10",
    ],
)
def test_parse_box_profile_rejects_non_box_or_impossible_sections(raw: str) -> None:
    with pytest.raises(MetadataResolutionError):
        parse_box_profile(raw)


@pytest.mark.parametrize("member", sorted(EXPECTED))
def test_resolve_all_twenty_title_blocks(member: str) -> None:
    path = INPUTS / f"{member}_拆板前.dxf"
    metadata = resolve_box_metadata(build_source_ir(path))
    h, b, tw, tf, length, material, scale = EXPECTED[member]

    assert metadata.member_mark.value == member
    assert metadata.profile.value.height == h
    assert metadata.profile.value.width == b
    assert metadata.profile.value.web_thickness == tw
    assert metadata.profile.value.flange_thickness == tf
    assert metadata.nominal_length.value == length
    assert metadata.material.value == material
    assert metadata.scale_denominator.value == scale
    assert metadata.title_group_id.startswith("insert:")
    assert all(
        field.source_id.startswith(metadata.title_group_id) for field in metadata.fields
    )


def test_conflicting_profile_evidence_fails_closed() -> None:
    source = build_source_ir(INPUTS / "2b1-cb-56_拆板前.dxf")
    conflicting = SourceEntityIR(
        source_id="insert:conflict/entity:profile",
        group_id="insert:conflict",
        handle="profile",
        kind="TEXT",
        layer="OtherObjectType",
        linetype="BYLAYER",
        text_raw="BOX900*900*20*20",
        text_decoded="BOX900*900*20*20",
    )

    with pytest.raises(MetadataResolutionError, match="conflicting BOX profiles"):
        resolve_box_metadata(replace(source, entities=(*source.entities, conflicting)))


def test_missing_material_in_title_group_fails_closed() -> None:
    source = build_source_ir(INPUTS / "2b1-cb-56_拆板前.dxf")
    filtered = tuple(
        replace(entity, text_raw="", text_decoded="")
        if entity.text_decoded == "Q420GJC-Z25"
        else entity
        for entity in source.entities
    )

    with pytest.raises(MetadataResolutionError, match="material"):
        resolve_box_metadata(replace(source, entities=filtered))


@pytest.mark.parametrize(
    ("path", "expected_scale"),
    [
        pytest.param(
            PROJECT_1_SAMPLE,
            20,
            marks=pytest.mark.skipif(
                not PROJECT_1_SAMPLE.is_file(),
                reason="可选的项目 1 BOX 测试语料在当前机器上不可用",
            ),
        ),
        (PROJECT_2_INPUTS / "w4e-cb-10.dxf", 20),
        (PROJECT_2_INPUTS / "w4e-cb-194.dxf", 30),
    ],
)
def test_drawing_scale_can_come_from_the_separate_sheet_group(
    path: Path,
    expected_scale: int,
) -> None:
    metadata = resolve_box_metadata(build_source_ir(path))

    assert metadata.scale_denominator.value == expected_scale
    assert not metadata.scale_denominator.source_id.startswith(metadata.title_group_id)


@pytest.mark.skipif(
    not PROJECT_1_SAMPLE.is_file(),
    reason="可选的项目 1 BOX 测试语料在当前机器上不可用",
)
def test_equal_sheet_scale_repetitions_are_one_semantic_value() -> None:
    source = build_source_ir(PROJECT_1_SAMPLE)
    duplicate = SourceEntityIR(
        source_id="insert:duplicate/entity:scale",
        group_id="insert:duplicate",
        handle="scale",
        kind="TEXT",
        layer="DrawingSheet",
        linetype="BYLAYER",
        text_raw="1:20",
        text_decoded="1:20",
    )

    metadata = resolve_box_metadata(
        replace(source, entities=(*source.entities, duplicate))
    )

    assert metadata.scale_denominator.value == 20


@pytest.mark.skipif(
    not PROJECT_1_SAMPLE.is_file(),
    reason="可选的项目 1 BOX 测试语料在当前机器上不可用",
)
def test_conflicting_sheet_scales_fail_closed() -> None:
    source = build_source_ir(PROJECT_1_SAMPLE)
    conflicting = SourceEntityIR(
        source_id="insert:conflict/entity:scale",
        group_id="insert:conflict",
        handle="scale",
        kind="TEXT",
        layer="DrawingSheet",
        linetype="BYLAYER",
        text_raw="1:30",
        text_decoded="1:30",
    )

    with pytest.raises(MetadataResolutionError, match="conflicting drawing scales"):
        resolve_box_metadata(replace(source, entities=(*source.entities, conflicting)))
