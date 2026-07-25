from __future__ import annotations

import pytest

from steel_dxf_split.box.manufacturing_ir import contour_polygon
from steel_dxf_split.box.metadata import resolve_box_metadata
from steel_dxf_split.box.source_ir import build_source_ir
from steel_dxf_split.box.view_frame import build_part_views
from steel_dxf_split.box.view_solver import enumerate_view_assignments
from steel_dxf_split.box.web_solver import WebDerivation, enumerate_web_outline_candidates
from tests.box_v1.paths import INPUTS


@pytest.mark.parametrize("member", ["2b2-cb-145", "2b2-cb-155", "2b2-cb-2"])
def test_web_search_retains_a_full_length_source_cap_cycle(member: str) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    candidates = enumerate_web_outline_candidates(assignment, metadata).candidates

    assert any(
        WebDerivation.CONNECTED_COURSE_CYCLE in candidate.derivations
        and candidate.longitudinal_span >= metadata.nominal_length.value - 0.1
        for candidate in candidates
    )


@pytest.mark.parametrize(
    ("member", "expected_spans"),
    [
        ("2t1-cb-95", (1_757.943, 926.465)),
        ("h-9-cb-94", (2_290.122, 1_423.227)),
        ("h-9-cb-133", (1_677.678, 693.677)),
        ("h-9-cb-73", (8_281.048, 7_327.610)),
    ],
)
def test_web_candidate_ir_combines_independent_source_derivations(
    member: str,
    expected_spans: tuple[float, ...],
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    result = enumerate_web_outline_candidates(assignment, metadata)
    actual_spans = tuple(candidate.longitudinal_span for candidate in result.candidates)

    for expected in expected_spans:
        assert min(abs(actual - expected) for actual in actual_spans) <= 0.02
    assert all(candidate.derivations for candidate in result.candidates)
    assert all(candidate.contour for candidate in result.candidates)


def test_cranked_course_search_reaches_both_physical_web_outlines() -> None:
    source = build_source_ir(INPUTS / "h-4-cb-37_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    result = enumerate_web_outline_candidates(assignment, metadata)
    areas = [candidate.area for candidate in result.candidates]

    assert min(abs(area - 9_919_225.109) for area in areas) <= 2.0
    assert min(abs(area - 10_241_418.959) for area in areas) <= 2.0


@pytest.mark.parametrize("member", ["2b1-cb-86", "2t1-cb-95", "h-9-cb-72"])
def test_straight_web_candidates_are_simple_and_keep_the_clear_course_width(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    candidates = enumerate_web_outline_candidates(assignment, metadata).candidates
    target = metadata.profile.value.web_clear_width

    assert candidates
    assert all(
        contour_polygon(candidate.contour).is_valid
        and contour_polygon(candidate.contour).exterior.is_simple
        for candidate in candidates
    )
    assert all(
        abs(candidate.transverse_span - target) <= max(1.1, target * 0.005)
        for candidate in candidates
    )
