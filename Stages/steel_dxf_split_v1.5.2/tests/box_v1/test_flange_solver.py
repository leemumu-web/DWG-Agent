from __future__ import annotations

import pytest

from steel_dxf_split.box.assembly import (
    _outer_flange_courses,
    _select_straight_flange_pair,
)
from steel_dxf_split.box.flange_solver import (
    FlangeDerivation,
    _enumerate_paired_course_cap_faces,
    enumerate_flange_outline_candidates,
)
from steel_dxf_split.box.metadata import resolve_box_metadata
from steel_dxf_split.box.projection_geometry import (
    CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,
)
from steel_dxf_split.box.source_ir import SourceEntityIR, build_source_ir
from steel_dxf_split.box.view_frame import PartViewIR, ViewFrame, build_part_views
from steel_dxf_split.box.view_solver import (
    ViewAssignmentCandidate,
    enumerate_view_assignments,
)
from tests.box_v1.paths import INPUTS


@pytest.mark.parametrize(
    ("member", "expected_spans"),
    [
        ("2b1-cb-86", (1_329.406, 1_163.033)),
        ("2b1-cb-91", (1_825.237, 923.495)),
        ("h-4-cb-37", (13_847.0, 13_833.0)),
        ("h-9-cb-279", (10_023.255, 9_013.254)),
        ("h-9-cb-69", (3_220.956, 3_149.326)),
        ("h-9-cb-73", (8_037.514, 7_787.637)),
        ("h-9-cb-94", (2_038.175, 2_018.038)),
    ],
)
def test_flange_candidate_ir_retains_both_full_width_plate_hypotheses(
    member: str,
    expected_spans: tuple[float, float],
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(
        build_part_views(source),
        metadata,
        source=source,
    )[0]

    result = enumerate_flange_outline_candidates(assignment, metadata)
    actual_spans = tuple(candidate.longitudinal_span for candidate in result.candidates)

    for expected in expected_spans:
        assert min(abs(actual - expected) for actual in actual_spans) <= 0.02
    assert all(
        candidate.transverse_span
        == pytest.approx(metadata.profile.value.width, abs=0.2)
        for candidate in result.candidates
    )


@pytest.mark.parametrize(
    ("member", "expected_span_area_pairs"),
    [
        (
            "h-9-cb-73",
            (
                (7_787.636673, 7_284_417.043480),
                (8_037.514251, 7_534_294.599753),
            ),
        ),
        (
            "h-9-cb-94",
            (
                (2_018.038213, 1_512_459.560550),
                (2_038.175216, 1_449_673.557050),
            ),
        ),
    ],
)
def test_flange_candidates_retain_source_shaped_non_rectangular_developments(
    member: str,
    expected_span_area_pairs: tuple[tuple[float, float], tuple[float, float]],
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    candidates = enumerate_flange_outline_candidates(assignment, metadata).candidates

    for expected_span, expected_area in expected_span_area_pairs:
        residuals = (
            abs(candidate.longitudinal_span - expected_span)
            + abs(candidate.area - expected_area) / metadata.profile.value.width
            for candidate in candidates
        )
        assert min(residuals) <= 0.05


def test_many_projection_faces_keep_and_select_the_notched_direct_flange() -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(
        build_part_views(source),
        metadata,
        source=source,
    )[0]

    result = enumerate_flange_outline_candidates(
        assignment,
        metadata,
        maximum_face_union_states=1,
        maximum_direct_faces=1,
    )
    maximal = tuple(
        candidate
        for candidate in result.candidates
        if FlangeDerivation.SOURCE_FACE_UNION in candidate.derivations
        and CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID in candidate.rule_ids
    )

    assert result.direct_face_search_pruned
    assert not result.direct_face_search_complete
    assert any(
        candidate.area == pytest.approx(3_634_600.0, abs=1.0)
        and len(candidate.projection.polygon.exterior.coords) - 1 == 8
        for candidate in maximal
    )
    selected = _select_straight_flange_pair(
        result.candidates,
        _outer_flange_courses(assignment, metadata),
        metadata,
    )
    assert all(candidate in maximal for candidate in selected)
    assert all(
        candidate.area == pytest.approx(3_634_600.0, abs=1.0) for candidate in selected
    )


def test_isolated_end_caps_cannot_fabricate_missing_longitudinal_edges() -> None:
    entities = (
        SourceEntityIR(
            source_id="left-cap",
            group_id="insert:test",
            handle="left-cap",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(0.0, 0.0),
            end=(0.0, 100.0),
        ),
        SourceEntityIR(
            source_id="right-cap",
            group_id="insert:test",
            handle="right-cap",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(40.0, 0.0),
            end=(40.0, 100.0),
        ),
    )
    view = PartViewIR(
        group_id="insert:test",
        block_name="*TEST",
        entities=entities,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=40.0,
            transverse_min=0.0,
            transverse_max=100.0,
        ),
    )
    assignment = ViewAssignmentCandidate(
        h_view=view,
        b_view=view,
        h_span_error=0.0,
        b_span_error=0.0,
        score=0.0,
    )

    assert not _enumerate_paired_course_cap_faces(
        assignment,
        target_transverse_mm=100.0,
        flange_thickness_mm=10.0,
    )


def test_arc_endpoint_chords_cannot_prove_straight_longitudinal_courses() -> None:
    entities = (
        SourceEntityIR(
            source_id="left-cap",
            group_id="insert:test",
            handle="left-cap",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(0.0, 0.0),
            end=(0.0, 100.0),
        ),
        SourceEntityIR(
            source_id="right-cap",
            group_id="insert:test",
            handle="right-cap",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(40.0, 0.0),
            end=(40.0, 100.0),
        ),
        SourceEntityIR(
            source_id="lower-arc",
            group_id="insert:test",
            handle="lower-arc",
            kind="ARC",
            layer="Part",
            linetype="XKITLINE00",
            center=(20.0, 0.0),
            radius=20.0,
            start_angle=180.0,
            end_angle=360.0,
        ),
        SourceEntityIR(
            source_id="upper-arc",
            group_id="insert:test",
            handle="upper-arc",
            kind="ARC",
            layer="Part",
            linetype="XKITLINE00",
            center=(20.0, 100.0),
            radius=20.0,
            start_angle=180.0,
            end_angle=360.0,
        ),
    )
    view = PartViewIR(
        group_id="insert:test",
        block_name="*TEST",
        entities=entities,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=40.0,
            transverse_min=0.0,
            transverse_max=100.0,
        ),
    )
    assignment = ViewAssignmentCandidate(
        h_view=view,
        b_view=view,
        h_span_error=0.0,
        b_span_error=0.0,
        score=0.0,
    )

    assert not _enumerate_paired_course_cap_faces(
        assignment,
        target_transverse_mm=100.0,
        flange_thickness_mm=10.0,
    )


@pytest.mark.parametrize(
    ("endpoint_gap_mm", "expected_count"),
    [(10.0, 1), (10.1, 0)],
)
def test_paired_cap_course_extension_is_bounded_by_flange_thickness(
    endpoint_gap_mm: float,
    expected_count: int,
) -> None:
    entities = (
        SourceEntityIR(
            source_id="left-cap",
            group_id="insert:test",
            handle="left-cap",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(0.0, 0.0),
            end=(0.0, 100.0),
        ),
        SourceEntityIR(
            source_id="right-cap",
            group_id="insert:test",
            handle="right-cap",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(40.0, 0.0),
            end=(40.0, 100.0),
        ),
        SourceEntityIR(
            source_id="lower-course",
            group_id="insert:test",
            handle="lower-course",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(endpoint_gap_mm, 0.0),
            end=(40.0 - endpoint_gap_mm, 0.0),
        ),
        SourceEntityIR(
            source_id="upper-course",
            group_id="insert:test",
            handle="upper-course",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(endpoint_gap_mm, 100.0),
            end=(40.0 - endpoint_gap_mm, 100.0),
        ),
    )
    view = PartViewIR(
        group_id="insert:test",
        block_name="*TEST",
        entities=entities,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=40.0,
            transverse_min=0.0,
            transverse_max=100.0,
        ),
    )
    assignment = ViewAssignmentCandidate(
        h_view=view,
        b_view=view,
        h_span_error=0.0,
        b_span_error=0.0,
        score=0.0,
    )

    candidates = _enumerate_paired_course_cap_faces(
        assignment,
        target_transverse_mm=100.0,
        flange_thickness_mm=10.0,
    )

    assert len(candidates) == expected_count
