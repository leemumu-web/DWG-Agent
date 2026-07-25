from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from steel_dxf_split.box.assembly import solve_complete_box
from steel_dxf_split.box.manufacturing_ir import PhysicalPlateRole
from steel_dxf_split.box.metadata import resolve_box_metadata
from steel_dxf_split.box.source_ir import build_source_ir
from steel_dxf_split.box.view_preprocessing import preprocess_box_views
from tests.box_v1.paths import INPUTS


PROJECT_BOX_ROOT = Path(r"D:\DevData\final dxf\BOX")
NEAR_SQUARE_PART_NUMBERS = (262, 268, 271, 320, 338, 340, 341)
SCALED_PART_NUMBERS = (307, 309, 310, 311, 312, 313, 314)
EXPECTED_GEOMETRY_SCALES = {
    307: 0.5,
    309: 2.0,
    310: 2.0,
    311: 2.5,
    312: 2.0,
    313: 2.0,
    314: 3.0,
}

pytestmark = pytest.mark.skipif(
    not PROJECT_BOX_ROOT.is_dir(),
    reason="项目 BOX DXF 数据集不可用",
)


def _project_box_path(part_number: int) -> Path:
    matches = tuple(PROJECT_BOX_ROOT.glob(f"*-cb-{part_number}.dxf"))
    assert len(matches) == 1, (
        f"预期恰好找到一张 cb-{part_number} DXF，实际找到 {len(matches)} 张："
        f"{[str(path) for path in matches]}"
    )
    return matches[0]


def _golden_input_path(member: str) -> Path:
    matches = tuple(INPUTS.glob(f"{member}_*.dxf"))
    assert len(matches) == 1
    return matches[0]


def _assert_unique_complete_four_plate_solution(part_number: int) -> None:
    source = build_source_ir(_project_box_path(part_number))
    result = solve_complete_box(source)

    assert len(result.hypotheses) == 1
    assert result.search_complete
    assert result.best.proof_report.search_complete
    assert not result.best.proof_report.blocking_obligation_ids
    assert {
        plate.role for plate in result.best.mir.physical_plates
    } == set(PhysicalPlateRole)


@pytest.mark.parametrize("part_number", NEAR_SQUARE_PART_NUMBERS)
def test_near_square_h_view_axis_is_resolved_by_complete_box_proof(
    part_number: int,
) -> None:
    _assert_unique_complete_four_plate_solution(part_number)


@pytest.mark.parametrize("part_number", SCALED_PART_NUMBERS)
def test_scaled_part_geometry_is_normalized_only_with_complete_dimension_evidence(
    part_number: int,
) -> None:
    _assert_unique_complete_four_plate_solution(part_number)


@pytest.mark.parametrize("member", ("2b1-cb-56", "2b2-cb-2"))
def test_sheet_scale_does_not_rescale_model_space_geometry(member: str) -> None:
    source = build_source_ir(_golden_input_path(member))
    metadata = resolve_box_metadata(source)

    assert preprocess_box_views(source, metadata).geometry_scale == 1.0


@pytest.mark.parametrize(
    ("part_number", "expected_scale"),
    EXPECTED_GEOMETRY_SCALES.items(),
)
def test_project_geometry_scale_requires_consistent_length_height_and_width(
    part_number: int,
    expected_scale: float,
) -> None:
    source = build_source_ir(_project_box_path(part_number))
    metadata = resolve_box_metadata(source)

    assert preprocess_box_views(source, metadata).geometry_scale == expected_scale


def test_geometry_scale_is_rejected_when_one_section_dimension_conflicts() -> None:
    source = build_source_ir(_project_box_path(309))
    metadata = resolve_box_metadata(source)
    conflicting_profile = replace(
        metadata.profile,
        value=replace(metadata.profile.value, width=160.0),
    )

    preprocessed = preprocess_box_views(
        source,
        replace(metadata, profile=conflicting_profile),
    )

    assert preprocessed.geometry_scale == 1.0
    assert preprocessed.source is source


def test_geometry_normalization_preserves_source_provenance() -> None:
    source = build_source_ir(_project_box_path(309))
    metadata = resolve_box_metadata(source)

    preprocessed = preprocess_box_views(source, metadata)

    assert preprocessed.geometry_scale == 2.0
    assert preprocessed.source.file_sha256 == source.file_sha256
    assert preprocessed.source.geometry_fingerprint == source.geometry_fingerprint
    assert tuple(entity.source_id for entity in preprocessed.source.entities) == tuple(
        entity.source_id for entity in source.entities
    )


@pytest.mark.parametrize("part_number", (2, 62))
def test_existing_cubic_member_keeps_its_original_search_result(
    part_number: int,
) -> None:
    source = build_source_ir(_project_box_path(part_number))

    result = solve_complete_box(source)

    assert len(result.hypotheses) == 2
    assert result.diagnostics == ()
    assert result.best.assignment.signature[:2] == ("insert:47", "insert:52")
