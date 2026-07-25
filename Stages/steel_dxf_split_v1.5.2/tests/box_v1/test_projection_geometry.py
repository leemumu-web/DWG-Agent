from __future__ import annotations

import pytest

from steel_dxf_split.box.course_graph import build_course_graph
from steel_dxf_split.box.metadata import resolve_box_metadata
from steel_dxf_split.box.projection_geometry import (
    CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,
    _k_shortest_simple_course_paths,
    enumerate_connected_inner_course_cycles,
    enumerate_endpoint_cap_path_cycles,
    enumerate_projection_course_virtual_cycles,
    enumerate_source_conserving_face_unions,
    enumerate_straight_inner_band_faces,
    polygonize_part_projection,
    search_connected_inner_course_cycles,
    search_source_conserving_face_unions,
)
from steel_dxf_split.box.source_ir import SourceEntityIR, build_source_ir
from steel_dxf_split.box.view_frame import (
    ViewFrame,
    build_part_views,
    derive_view_frame,
)
from steel_dxf_split.box.view_solver import enumerate_view_assignments
from tests.box_v1.paths import INPUTS


@pytest.mark.parametrize("member", ["2b2-cb-145", "2b2-cb-155", "2b2-cb-2"])
def test_local_outer_cap_alternatives_close_a_full_length_inner_course_cycle(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]
    profile = metadata.profile.value
    endpoint_tolerance = min(
        3.1,
        max(
            0.15,
            0.1 * min(profile.web_thickness, profile.flange_thickness),
        ),
    )

    candidates = enumerate_connected_inner_course_cycles(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=profile.web_clear_width,
        endpoint_tolerance_mm=endpoint_tolerance,
    )

    assert any(
        candidate.longitudinal_span >= metadata.nominal_length.value - 0.1
        for candidate in candidates
    )


def _line(
    source_id: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    hidden: bool = False,
) -> SourceEntityIR:
    return SourceEntityIR(
        source_id=source_id,
        group_id="insert:test",
        handle=source_id,
        kind="LINE",
        layer="Part",
        linetype="XKITLINE04" if hidden else "XKITLINE00",
        start=start,
        end=end,
    )


def test_polygonizer_keeps_hidden_linework_as_source_context() -> None:
    entities = (
        _line("bottom", (0.0, 0.0), (100.0, 0.0)),
        _line("right", (100.0, 0.0), (100.0, 20.0)),
        _line("top", (100.0, 20.0), (0.0, 20.0)),
        _line("left", (0.0, 20.0), (0.0, 0.0)),
        _line("middle", (50.0, 0.0), (50.0, 20.0), hidden=True),
    )
    frame = derive_view_frame(entities)

    visible = polygonize_part_projection(entities, frame, include_hidden=False)
    all_faces = polygonize_part_projection(entities, frame, include_hidden=True)

    assert len(visible) == 1
    assert len(all_faces) == 2
    assert sum(face.area for face in all_faces) == pytest.approx(visible[0].area)


def test_source_endpoint_rule_rejects_projection_line_as_a_physical_end_cap() -> None:
    entities = (
        _line("bottom", (0.0, 0.0), (100.0, 0.0)),
        _line("right", (100.0, 0.0), (100.0, 20.0)),
        _line("top", (100.0, 20.0), (0.0, 20.0)),
        _line("left", (0.0, 20.0), (0.0, 0.0)),
        _line("crossing-overlay", (50.0, -5.0), (50.0, 25.0), hidden=True),
    )
    frame = derive_view_frame(entities)
    candidates = enumerate_source_conserving_face_unions(
        entities,
        frame,
        target_transverse_mm=20.0,
    )

    assert len(candidates) == 1
    assert candidates[0].polygon.bounds == pytest.approx((-50.0, -10.0, 50.0, 10.0))
    assert "crossing-overlay" not in candidates[0].boundary_source_ids


def test_connected_maximal_material_face_survives_one_state_budget() -> None:
    """Hidden overlays may partition a notched plate, never fill its notch."""

    outline = (
        (0.0, 0.0),
        (120.0, 0.0),
        (120.0, 40.0),
        (75.0, 40.0),
        (75.0, 20.0),
        (45.0, 20.0),
        (45.0, 40.0),
        (0.0, 40.0),
    )
    boundary = tuple(
        _line(f"boundary-{index}", start, end)
        for index, (start, end) in enumerate(
            zip(outline, (*outline[1:], outline[0]), strict=True)
        )
    )
    overlays = tuple(
        _line(
            f"overlay-{station}",
            (float(station), -5.0),
            (float(station), 45.0),
            hidden=True,
        )
        for station in range(1, 120)
    )
    entities = (*boundary, *overlays)
    frame = ViewFrame(
        origin=(0.0, 0.0),
        longitudinal_axis=(1.0, 0.0),
        transverse_axis=(0.0, 1.0),
        longitudinal_min=0.0,
        longitudinal_max=120.0,
        transverse_min=0.0,
        transverse_max=40.0,
    )

    faces = polygonize_part_projection(entities, frame, include_hidden=True)
    search = search_source_conserving_face_unions(
        entities,
        frame,
        target_transverse_mm=40.0,
        maximum_states=1,
    )
    candidates = search.candidates

    assert len(faces) > 100
    assert not search.subset_search_complete
    assert search.state_budget_exhausted
    matching = min(
        candidates, key=lambda candidate: abs(candidate.polygon.area - 4_200.0)
    )
    assert matching.polygon.area == pytest.approx(4_200.0, abs=0.01)
    assert len(matching.polygon.exterior.coords) - 1 == 8
    assert matching.polygon.area < 120.0 * 40.0


def test_visible_material_divider_is_not_erased_by_maximal_face_union() -> None:
    entities = (
        _line("bottom", (0.0, 0.0), (100.0, 0.0)),
        _line("right", (100.0, 0.0), (100.0, 50.0)),
        _line("top", (100.0, 50.0), (0.0, 50.0)),
        _line("left", (0.0, 50.0), (0.0, 0.0)),
        _line("physical-divider", (50.0, 0.0), (50.0, 50.0)),
    )
    frame = derive_view_frame(entities)

    search = search_source_conserving_face_unions(
        entities,
        frame,
        target_transverse_mm=50.0,
        maximum_states=0,
        run_subset_search=False,
    )

    assert not any(
        CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID in candidate.rule_ids
        for candidate in search.candidates
    )


def test_distinct_equal_length_arcs_are_not_collapsed_as_duplicate_paths() -> None:
    entities = (
        SourceEntityIR(
            source_id="lower-arc",
            group_id="insert:test",
            handle="lower-arc",
            kind="ARC",
            layer="Part",
            linetype="XKITLINE00",
            center=(5.0, 0.0),
            radius=5.0,
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
            center=(5.0, 0.0),
            radius=5.0,
            start_angle=0.0,
            end_angle=180.0,
        ),
    )
    frame = ViewFrame(
        origin=(0.0, 0.0),
        longitudinal_axis=(1.0, 0.0),
        transverse_axis=(0.0, 1.0),
        longitudinal_min=0.0,
        longitudinal_max=10.0,
        transverse_min=-5.0,
        transverse_max=5.0,
    )
    graph = build_course_graph(entities, frame)
    start_node = min(graph.nodes, key=lambda node: node.point[0]).node_id
    end_node = max(graph.nodes, key=lambda node: node.point[0]).node_id

    result = _k_shortest_simple_course_paths(
        graph,
        start_node,
        end_node,
        maximum_paths=8,
        maximum_length_mm=100.0,
    )

    assert result.complete
    assert {path[0][0].source_ids for path in result.paths if len(path) == 1} == {
        ("lower-arc",),
        ("upper-arc",),
    }

    path_limited = _k_shortest_simple_course_paths(
        graph,
        start_node,
        end_node,
        maximum_paths=1,
        maximum_length_mm=100.0,
    )
    expansion_limited = _k_shortest_simple_course_paths(
        graph,
        start_node,
        end_node,
        maximum_paths=8,
        maximum_length_mm=100.0,
        maximum_expansions=1,
    )

    assert len(path_limited.paths) == 1
    assert not path_limited.complete
    assert not expansion_limited.complete


def _real_web_candidates(member: str):
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]
    return enumerate_source_conserving_face_unions(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=metadata.profile.value.web_clear_width,
    )


def test_real_unequal_web_projection_recovers_two_direct_physical_faces() -> None:
    candidates = _real_web_candidates("2b1-cb-86")
    two_largest = candidates[:2]

    assert len(two_largest) == 2
    assert [candidate.polygon.area for candidate in two_largest] == pytest.approx(
        [708072.022, 594880.613],
        abs=1.0,
    )
    assert [candidate.longitudinal_span for candidate in two_largest] == pytest.approx(
        [997.284567, 851.88543],
        abs=0.01,
    )
    assert all(candidate.source_conserved for candidate in two_largest)


def test_real_symmetric_web_projection_deduplicates_one_physical_geometry() -> None:
    candidates = _real_web_candidates("h-3-cb-2")

    assert len(candidates) == 1
    assert candidates[0].longitudinal_span == pytest.approx(3700.0, abs=0.01)
    assert candidates[0].transverse_span == pytest.approx(910.0, abs=0.01)


def test_faceted_and_exact_arc_overlays_do_not_hide_the_second_web() -> None:
    source = build_source_ir(INPUTS / "2b2-cb-155_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    faces = polygonize_part_projection(
        assignment.h_view.entities,
        assignment.h_view.frame,
        include_hidden=True,
    )
    candidates = enumerate_source_conserving_face_unions(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=metadata.profile.value.web_clear_width,
        maximum_states=10_000,
    )

    # Tekla exports the same 35 mm corner once as a true ARC and once as a
    # short faceted chain.  That is duplicate drawing evidence, not a nest of
    # physical sub-millimetre plate faces.
    assert len(faces) <= 6
    areas = [candidate.polygon.area for candidate in candidates]
    assert min(abs(area - 13_431_974.998) for area in areas) <= 800.0
    assert min(abs(area - 12_926_742.498) for area in areas) <= 800.0


@pytest.mark.parametrize(
    ("member", "expected_span", "expected_area"),
    [
        ("h-9-cb-116", 5_785.817274, 1_502_583.351),
        ("h-9-cb-69", 3_403.512469, 697_692.772),
        ("h-9-cb-94", 2_290.122, 555_949.962),
        ("2b1-cb-91", 1_801.506867, 974_235.282),
    ],
)
def test_hidden_inner_courses_bound_a_web_inside_the_visible_outline(
    member: str,
    expected_span: float,
    expected_area: float,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    candidates = enumerate_straight_inner_band_faces(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=metadata.profile.value.web_clear_width,
    )

    assert candidates
    matching = min(
        candidates,
        key=lambda candidate: abs(candidate.longitudinal_span - expected_span),
    )
    assert matching.longitudinal_span == pytest.approx(expected_span, abs=0.01)
    assert matching.polygon.area == pytest.approx(expected_area, abs=2.0)
    assert matching.source_conserved


def test_connected_inner_courses_follow_source_end_chains_instead_of_extending() -> (
    None
):
    source = build_source_ir(INPUTS / "h-9-cb-73_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    candidates = enumerate_connected_inner_course_cycles(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=metadata.profile.value.web_clear_width,
    )

    matching = min(
        candidates,
        key=lambda candidate: abs(candidate.longitudinal_span - 7_327.610260),
    )
    assert matching.longitudinal_span == pytest.approx(7_327.610260, abs=0.01)
    # This is still projection geometry: small Tekla fillet ARCs remain curved
    # until the explicit projection-to-manufacturing lowering pass.
    assert matching.polygon.area == pytest.approx(1_583_218.223, abs=2.0)
    assert matching.source_conserved


@pytest.mark.parametrize("member", ["h-9-cb-72", "h-9-cb-279"])
def test_connected_inner_course_search_reports_complete_source_domain(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    result = search_connected_inner_course_cycles(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=metadata.profile.value.web_clear_width,
    )

    assert result.complete
    assert result.candidates


def test_parallel_long_course_endpoints_keep_multiple_source_path_alternatives() -> (
    None
):
    source = build_source_ir(INPUTS / "h-9-cb-73_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    candidates = enumerate_endpoint_cap_path_cycles(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=metadata.profile.value.web_clear_width,
    )
    spans = [candidate.longitudinal_span for candidate in candidates]

    assert min(abs(span - 7_327.610260) for span in spans) <= 0.01
    assert min(abs(span - 8_281.047553) for span in spans) <= 0.01


@pytest.mark.parametrize("member", ["h-4-cb-37", "h-4-cb-38"])
def test_open_cranked_hidden_courses_close_only_through_bounded_virtual_caps(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]

    candidates = enumerate_projection_course_virtual_cycles(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=metadata.profile.value.web_clear_width,
    )
    areas = [candidate.polygon.area for candidate in candidates]

    assert min(abs(area - 9_919_225.109) for area in areas) <= 2.0
    assert min(abs(area - 10_241_418.959) for area in areas) <= 2.0
    assert all(candidate.source_conserved for candidate in candidates)
