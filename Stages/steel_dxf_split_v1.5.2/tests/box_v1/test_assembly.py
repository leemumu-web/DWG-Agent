from __future__ import annotations

from dataclasses import replace
from math import hypot
from pathlib import Path

import pytest

import steel_dxf_split.box.assembly as assembly_module
from steel_dxf_split.box.assembly import (
    _exact_h_course_maximal_flange_pair_dominates,
    _is_explicit_outward_development,
    _outer_flange_courses,
    solve_complete_box,
)
from steel_dxf_split.box.flange_solver import (
    FlangeDerivation,
    enumerate_flange_outline_candidates,
)
from steel_dxf_split.box.manufacturing_ir import (
    PhysicalPlateRole,
    contour_polygon,
    rectangle_contour,
)
from steel_dxf_split.box.metadata import resolve_box_metadata
from steel_dxf_split.box.projection_geometry import (
    CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,
)
from steel_dxf_split.box.source_ir import build_source_ir
from steel_dxf_split.box.view_frame import build_part_views
from steel_dxf_split.box.view_solver import enumerate_view_assignments
from steel_dxf_split.box.web_solver import (
    WebDerivation,
    enumerate_web_outline_candidates,
)
from tests.box_v1.paths import INPUTS, PROJECT_1_INPUTS

PROJECT_1_SAMPLE = PROJECT_1_INPUTS / "w3-cb-57_拆板前.dxf"


@pytest.mark.parametrize(
    "member",
    [
        "2b1-cb-56",
        "2b1-cb-86",
        "2b2-cb-145",
        "2t1-cb-95",
        "h-9-cb-73",
        "h-9-cb-94",
    ],
)
def test_complete_solver_freezes_exactly_four_source_backed_physical_roles(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")

    result = solve_complete_box(source)
    best = result.best

    assert result.search_complete
    assert {plate.role for plate in best.mir.physical_plates} == set(PhysicalPlateRole)
    assert all(
        contour_polygon(plate.outer_segments).area > 0
        for plate in best.mir.physical_plates
    )
    assert all(plate.role_evidence.source_ids for plate in best.mir.physical_plates)
    assert best.proof_report.disposition.value == "auto_accept"


@pytest.mark.skipif(
    not PROJECT_1_SAMPLE.is_file(),
    reason="可选的项目 1 BOX 测试语料在当前机器上不可用",
)
def test_drawing_scale_does_not_change_manufacturing_geometry() -> None:
    source = build_source_ir(PROJECT_1_SAMPLE)
    scale_source_id = resolve_box_metadata(source).scale_denominator.source_id
    altered_entities = tuple(
        replace(entity, text_raw="1:777", text_decoded="1:777")
        if entity.source_id == scale_source_id
        else entity
        for entity in source.entities
    )
    altered_source = replace(source, entities=altered_entities)

    assert resolve_box_metadata(altered_source).scale_denominator.value == 777
    assert (
        solve_complete_box(altered_source).best.mir.fingerprint
        == solve_complete_box(source).best.mir.fingerprint
    )


def test_square_box_view_tie_is_resolved_by_complete_geometry_and_bolt_evidence() -> (
    None
):
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")

    result = solve_complete_box(source)
    best = result.best
    by_role = {plate.role: plate for plate in best.mir.physical_plates}
    search_proof = next(
        obligation
        for obligation in best.proof_report.obligations
        if obligation.obligation_id == "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN"
    )

    assert result.search_complete
    assert best.proof_report.disposition.value == "auto_accept"
    assert (
        search_proof.diagnostic_code
        == "BOX.SEARCH.INDEPENDENT_SOURCE_TOPOLOGY_DOMINATES"
    )
    assert {evidence.measured for evidence in search_proof.evidence} == {
        "exact_h_course_maximal_flange_dominates",
        "independent_source_topology_dominates",
    }
    assert len(by_role[PhysicalPlateRole.WEB_LEFT].circular_cuts) == 8
    assert len(by_role[PhysicalPlateRole.WEB_RIGHT].circular_cuts) == 8
    assert not by_role[PhysicalPlateRole.FLANGE_TOP].circular_cuts
    assert not by_role[PhysicalPlateRole.FLANGE_BOTTOM].circular_cuts


def test_square_hb_role_does_not_depend_on_search_pruning_workload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")

    def web_search(assignment, metadata):
        return replace(
            enumerate_web_outline_candidates(assignment, metadata),
            direct_face_search_pruned=assignment.h_view.group_id == "insert:AF",
        )

    def flange_search(assignment, metadata):
        return replace(
            enumerate_flange_outline_candidates(assignment, metadata),
            direct_face_search_pruned=assignment.h_view.group_id == "insert:AF",
        )

    monkeypatch.setattr(assembly_module, "enumerate_web_outline_candidates", web_search)
    monkeypatch.setattr(
        assembly_module,
        "enumerate_flange_outline_candidates",
        flange_search,
    )

    best = solve_complete_box(source).best
    by_role = {plate.role: plate for plate in best.mir.physical_plates}

    assert best.assignment.h_view.group_id == "insert:AF"
    assert len(by_role[PhysicalPlateRole.WEB_LEFT].circular_cuts) == 8
    assert not by_role[PhysicalPlateRole.FLANGE_TOP].circular_cuts


def test_part_mark_h_role_outranks_divergent_search_completeness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")

    def web_search(assignment, metadata):
        return replace(
            enumerate_web_outline_candidates(assignment, metadata),
            direct_face_search_complete=assignment.h_view.group_id != "insert:AF",
        )

    def flange_search(assignment, metadata):
        return replace(
            enumerate_flange_outline_candidates(assignment, metadata),
            direct_face_search_complete=assignment.h_view.group_id != "insert:AF",
        )

    monkeypatch.setattr(assembly_module, "enumerate_web_outline_candidates", web_search)
    monkeypatch.setattr(
        assembly_module,
        "enumerate_flange_outline_candidates",
        flange_search,
    )
    monkeypatch.setattr(
        assembly_module,
        "_exact_h_course_maximal_flange_pair_dominates",
        lambda *_args: False,
    )

    result = solve_complete_box(source)
    correct = next(
        hypothesis
        for hypothesis in result.hypotheses
        if hypothesis.assignment.h_view.group_id == "insert:AF"
    )
    reversed_assignment = next(
        hypothesis
        for hypothesis in result.hypotheses
        if hypothesis.assignment.h_view.group_id == "insert:2AA"
    )

    assert not correct.proof_report.search_complete
    assert reversed_assignment.proof_report.search_complete
    assert result.best is correct


def test_hidden_overlay_maximal_flange_proves_exact_h_course_dominance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")

    monkeypatch.setattr(
        assembly_module,
        "enumerate_web_outline_candidates",
        lambda assignment, metadata: replace(
            enumerate_web_outline_candidates(assignment, metadata),
            direct_face_search_complete=True,
        ),
    )
    monkeypatch.setattr(
        assembly_module,
        "enumerate_flange_outline_candidates",
        lambda assignment, metadata: enumerate_flange_outline_candidates(
            assignment,
            metadata,
            maximum_face_union_states=1,
            maximum_direct_faces=1,
        ),
    )

    best = solve_complete_box(source).best
    search_proof = next(
        obligation
        for obligation in best.proof_report.obligations
        if obligation.obligation_id == "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN"
    )

    assert best.proof_report.search_complete
    assert best.proof_report.disposition.value == "auto_accept"
    assert search_proof.status.value == "pass"
    assert (
        search_proof.diagnostic_code
        == "BOX.SEARCH.EXACT_H_COURSE_MAXIMAL_FLANGE_DOMINATES"
    )
    assert {evidence.measured for evidence in search_proof.evidence} == {
        "complete",
        "exact_h_course_maximal_flange_dominates",
    }
    assert all(
        CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID in candidate.rule_ids
        for candidate in best.flange_candidates
    )


def test_maximal_flange_dominance_requires_exact_h_course_span() -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(
        build_part_views(source),
        metadata,
        source=source,
    )[0]
    search = enumerate_flange_outline_candidates(
        assignment,
        metadata,
        maximum_face_union_states=1,
        maximum_direct_faces=1,
    )
    selected = assembly_module._select_straight_flange_pair(
        search.candidates,
        _outer_flange_courses(assignment, metadata),
        metadata,
    )

    assert _exact_h_course_maximal_flange_pair_dominates(
        assignment,
        metadata,
        selected,
    )

    shortened = []
    for candidate in selected:
        min_x, min_y, max_x, max_y = contour_polygon(candidate.contour).bounds
        shortened.append(
            replace(
                candidate,
                contour=rectangle_contour(
                    min_x,
                    min_y,
                    max_x - 1.0,
                    max_y,
                    candidate.contour[0].evidence,
                ),
            )
        )
    assert not _exact_h_course_maximal_flange_pair_dominates(
        assignment,
        metadata,
        tuple(shortened),
    )


def test_exact_source_maximal_flange_outranks_same_span_derived_candidate() -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(
        build_part_views(source),
        metadata,
        source=source,
    )[0]
    search = enumerate_flange_outline_candidates(
        assignment,
        metadata,
        maximum_face_union_states=1,
        maximum_direct_faces=1,
    )
    maximal = next(
        candidate
        for candidate in search.candidates
        if FlangeDerivation.SOURCE_FACE_UNION in candidate.derivations
        and CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID in candidate.rule_ids
        and candidate.area == pytest.approx(3_634_600.0, abs=1.0)
    )
    derived = replace(
        maximal,
        candidate_id="same-span-derived",
        derivations=(FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT,),
        rule_ids=(),
    )

    selected = assembly_module._select_straight_flange_pair(
        (maximal, derived),
        _outer_flange_courses(assignment, metadata),
        metadata,
    )

    assert selected == (maximal, maximal)

    min_x, min_y, max_x, max_y = contour_polygon(maximal.contour).bounds
    outward_contour = rectangle_contour(
        min_x,
        min_y,
        max_x + 5.0,
        max_y,
        maximal.contour[0].evidence,
    )
    outward = replace(
        maximal,
        candidate_id="outward-source-union",
        contour=outward_contour,
        projection=replace(
            maximal.projection,
            polygon=contour_polygon(outward_contour),
            rule_ids=(),
        ),
        rule_ids=(),
    )

    selected_with_outward = assembly_module._select_straight_flange_pair(
        (maximal, outward),
        _outer_flange_courses(assignment, metadata),
        metadata,
    )

    assert selected_with_outward == (maximal, maximal)


def test_pruned_direct_search_without_selected_maximal_face_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")

    monkeypatch.setattr(
        assembly_module,
        "enumerate_web_outline_candidates",
        lambda assignment, metadata: replace(
            enumerate_web_outline_candidates(assignment, metadata),
            direct_face_search_complete=True,
        ),
    )

    def pruned_flange_search(assignment, metadata):
        result = enumerate_flange_outline_candidates(
            assignment,
            metadata,
            maximum_face_union_states=1,
            maximum_direct_faces=1,
        )
        return replace(
            result,
            direct_face_search_complete=False,
            candidates=tuple(
                candidate
                for candidate in result.candidates
                if CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID not in candidate.rule_ids
            ),
        )

    monkeypatch.setattr(
        assembly_module,
        "enumerate_flange_outline_candidates",
        pruned_flange_search,
    )

    best = solve_complete_box(source).best
    search_proof = next(
        obligation
        for obligation in best.proof_report.obligations
        if obligation.obligation_id == "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN"
    )

    assert not best.proof_report.search_complete
    assert best.proof_report.disposition.value != "auto_accept"
    assert search_proof.status.value == "incomplete"
    assert (
        search_proof.diagnostic_code
        == "BOX.SEARCH.DIRECT_SOURCE_FACE_SUBSEARCH_INCOMPLETE"
    )


def test_non_conserved_independent_topology_cannot_compensate_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")

    def unsafe_web_search(assignment, metadata):
        result = enumerate_web_outline_candidates(assignment, metadata)
        return replace(
            result,
            direct_face_search_complete=False,
            candidates=tuple(
                replace(
                    candidate,
                    projection=replace(
                        candidate.projection,
                        source_conserved=False,
                    ),
                )
                for candidate in result.candidates
            ),
        )

    monkeypatch.setattr(
        assembly_module,
        "enumerate_web_outline_candidates",
        unsafe_web_search,
    )

    best = solve_complete_box(source).best

    assert not best.proof_report.search_complete
    assert best.proof_report.disposition.value != "auto_accept"


def test_web_maximal_marker_alone_cannot_compensate_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")

    def maximal_only_web_search(assignment, metadata):
        result = enumerate_web_outline_candidates(assignment, metadata)
        return replace(
            result,
            direct_face_search_complete=False,
            connected_course_search_complete=False,
            candidates=tuple(
                replace(
                    candidate,
                    derivations=(WebDerivation.SOURCE_FACE_UNION,),
                    projection=replace(
                        candidate.projection,
                        source_conserved=True,
                        rule_ids=(CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,),
                    ),
                )
                for candidate in result.candidates
            ),
        )

    monkeypatch.setattr(
        assembly_module,
        "enumerate_web_outline_candidates",
        maximal_only_web_search,
    )

    best = solve_complete_box(source).best

    assert not best.proof_report.search_complete
    assert best.proof_report.disposition.value != "auto_accept"


def test_budgeted_cycle_derivation_cannot_compensate_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")

    def budgeted_web_search(assignment, metadata):
        result = enumerate_web_outline_candidates(assignment, metadata)
        return replace(
            result,
            direct_face_search_complete=False,
            candidates=tuple(
                replace(
                    candidate,
                    derivations=(WebDerivation.ENDPOINT_CAP_PATH_CYCLE,),
                )
                for candidate in result.candidates
            ),
        )

    monkeypatch.setattr(
        assembly_module,
        "enumerate_web_outline_candidates",
        budgeted_web_search,
    )

    best = solve_complete_box(source).best

    assert not best.proof_report.search_complete
    assert best.proof_report.disposition.value != "auto_accept"


def test_truncated_connected_course_search_cannot_compensate_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "h-9-cb-72_拆板前.dxf")

    def truncated_web_search(assignment, metadata):
        result = enumerate_web_outline_candidates(assignment, metadata)
        return replace(
            result,
            direct_face_search_complete=False,
            connected_course_search_complete=False,
        )

    monkeypatch.setattr(
        assembly_module,
        "enumerate_web_outline_candidates",
        truncated_web_search,
    )

    best = solve_complete_box(source).best

    assert not best.proof_report.search_complete
    assert best.proof_report.disposition.value != "auto_accept"


@pytest.mark.parametrize("member", ["h-4-cb-37", "h-4-cb-38"])
def test_cranked_box_bolt_pattern_is_not_copied_to_the_other_three_plates(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")

    best = solve_complete_box(source).best
    cut_counts = sorted(len(plate.circular_cuts) for plate in best.mir.physical_plates)

    assert best.proof_report.search_complete
    assert best.proof_report.disposition.value == "auto_accept"
    assert cut_counts == [0, 0, 0, 14]


@pytest.mark.parametrize("member", ["h-9-cb-72", "h-9-cb-279"])
def test_complete_connected_web_topology_dominates_incomplete_face_subsets(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")

    best = solve_complete_box(source).best
    search_proof = next(
        obligation
        for obligation in best.proof_report.obligations
        if obligation.obligation_id == "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN"
    )

    assert best.proof_report.search_complete
    assert best.proof_report.disposition.value == "auto_accept"
    assert any(
        evidence.measured == "complete_connected_course_topology_dominates"
        for evidence in search_proof.evidence
    )


def test_complete_course_domain_certifies_source_boundary_web_pair() -> None:
    """A bounded subset budget cannot reject two complete source outlines."""

    source = build_source_ir(INPUTS / "2b2-cb-2_拆板前.dxf")

    best = solve_complete_box(source).best
    search_proof = next(
        obligation
        for obligation in best.proof_report.obligations
        if obligation.obligation_id == "BOX.PROOF.SEARCH.DIRECT_SOURCE_FACE_DOMAIN"
    )

    assert best.proof_report.search_complete
    assert best.proof_report.disposition.value == "auto_accept"
    assert search_proof.status.value == "pass"
    assert (
        search_proof.diagnostic_code
        == "BOX.SEARCH.DIRECT_SOURCE_BOUNDARY_WITH_COMPLETE_COURSE_DOMAIN"
    )
    assert any(
        evidence.measured == "direct_source_boundary_with_complete_course_domain"
        for evidence in search_proof.evidence
    )


def test_complete_course_flag_without_domain_witness_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = build_source_ir(INPUTS / "2b2-cb-2_拆板前.dxf")

    def search_without_course_domain_witness(assignment, metadata):
        result = enumerate_web_outline_candidates(assignment, metadata)
        return replace(
            result,
            direct_face_search_complete=False,
            connected_course_search_complete=True,
            candidates=tuple(
                candidate
                for candidate in result.candidates
                if WebDerivation.CONNECTED_COURSE_CYCLE not in candidate.derivations
            ),
        )

    monkeypatch.setattr(
        assembly_module,
        "enumerate_web_outline_candidates",
        search_without_course_domain_witness,
    )

    best = solve_complete_box(source).best

    assert not best.proof_report.search_complete
    assert best.proof_report.disposition.value != "auto_accept"


def test_equivalent_straight_box_surfaces_remain_two_physical_roles() -> None:
    source = build_source_ir(INPUTS / "2b1-cb-56_拆板前.dxf")

    plates = {
        plate.role: plate
        for plate in solve_complete_box(source).best.mir.physical_plates
    }

    assert contour_polygon(
        plates[PhysicalPlateRole.WEB_LEFT].outer_segments
    ).equals_exact(
        contour_polygon(plates[PhysicalPlateRole.WEB_RIGHT].outer_segments),
        0.001,
    )
    assert contour_polygon(
        plates[PhysicalPlateRole.FLANGE_TOP].outer_segments
    ).equals_exact(
        contour_polygon(plates[PhysicalPlateRole.FLANGE_BOTTOM].outer_segments),
        0.001,
    )


@pytest.mark.parametrize(
    "path",
    sorted(INPUTS.glob("*_拆板前.dxf")),
    ids=lambda path: path.stem.removesuffix("_拆板前"),
)
def test_straight_box_flange_assignment_never_shortens_an_outer_h_course(
    path: Path,
) -> None:
    """Cross-view lowering may develop a course outwards, never trim it inwards."""

    source = build_source_ir(path)
    metadata = resolve_box_metadata(source)
    best = solve_complete_box(source).best
    if (
        best.assignment.h_view.frame.transverse_span
        > metadata.profile.value.height * 1.5
    ):
        pytest.skip("cranked BOX uses paired-web neutral-axis lowering")

    bottom_course, top_course = _outer_flange_courses(best.assignment, metadata)
    top, bottom = best.flange_candidates

    assert top.longitudinal_span >= top_course.length - 0.02
    assert bottom.longitudinal_span >= bottom_course.length - 0.02


def test_hidden_h_courses_participate_in_outer_flange_course_resolution() -> None:
    """Tekla hidden courses are geometry evidence, not decorative entities."""

    source = build_source_ir(INPUTS / "2b1-cb-91_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = solve_complete_box(source).best.assignment
    frame = assignment.h_view.frame
    minimum_length = max(
        metadata.profile.value.width * 0.25,
        metadata.nominal_length.value * 0.10,
    )
    source_courses: list[tuple[float, float]] = []
    for entity in assignment.h_view.entities:
        if entity.kind != "LINE" or entity.start is None or entity.end is None:
            continue
        start = frame.world_to_local(entity.start)
        end = frame.world_to_local(entity.end)
        length = hypot(end[0] - start[0], end[1] - start[1])
        if (
            length >= minimum_length
            and abs(end[0] - start[0]) / max(length, 1e-9) >= 0.965925826
        ):
            source_courses.append((length, (start[1] + end[1]) / 2.0))

    # DXF coordinates at a nominal thickness plane can differ by a few
    # hundredths after Tekla's projection transforms.
    window = max(1.0, metadata.profile.value.flange_thickness) + 0.05
    minimum_y = min(transverse for _, transverse in source_courses)
    maximum_y = max(transverse for _, transverse in source_courses)
    expected_bottom = max(
        length
        for length, transverse in source_courses
        if transverse <= minimum_y + window
    )
    expected_top = max(
        length
        for length, transverse in source_courses
        if transverse >= maximum_y - window
    )
    bottom, top = _outer_flange_courses(assignment, metadata)

    assert bottom.length == pytest.approx(expected_bottom, abs=0.001)
    assert top.length == pytest.approx(expected_top, abs=0.001)


@pytest.mark.parametrize("member", ["2b2-cb-145", "2b2-cb-155", "2b2-cb-2"])
def test_direct_b_face_topology_outranks_an_isolated_short_h_station(
    member: str,
) -> None:
    """A synthetic station rectangle cannot beat a complete B-view face."""

    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    best = solve_complete_box(source).best
    courses = _outer_flange_courses(best.assignment, metadata)
    complete_face_span = max(course.length for course in courses)

    assert all(
        candidate.longitudinal_span == pytest.approx(complete_face_span, abs=0.02)
        for candidate in best.flange_candidates
    )


@pytest.mark.parametrize("member", ["2b2-cb-145", "2b2-cb-155", "2b2-cb-2"])
def test_one_complete_b_outline_can_prove_both_equivalent_flange_roles(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    best = solve_complete_box(source).best
    bottom_course, top_course = _outer_flange_courses(best.assignment, metadata)
    top, bottom = best.flange_candidates

    assert contour_polygon(top.contour).equals_exact(
        contour_polygon(bottom.contour),
        0.001,
    )
    assert _is_explicit_outward_development(
        top,
        top.longitudinal_span - top_course.length,
    ) or _is_explicit_outward_development(
        bottom,
        bottom.longitudinal_span - bottom_course.length,
    )


def test_unexplained_positive_flange_offset_is_minimized() -> None:
    """A larger B span needs an explicit cap-extension/offset derivation."""

    source = build_source_ir(INPUTS / "2b1-cb-91_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    best = solve_complete_box(source).best
    bottom_course, _ = _outer_flange_courses(best.assignment, metadata)
    _, bottom_flange = best.flange_candidates

    assert bottom_flange.longitudinal_span - bottom_course.length <= 0.05


def test_flange_pair_preserves_longitudinal_end_order_across_views() -> None:
    """Top/bottom B faces must preserve the H-course end-order relation."""

    source = build_source_ir(INPUTS / "h-9-cb-69_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    best = solve_complete_box(source).best
    bottom_course, top_course = _outer_flange_courses(best.assignment, metadata)
    top_flange, bottom_flange = best.flange_candidates
    course_delta = top_course.longitudinal_center - bottom_course.longitudinal_center
    candidate_delta = (
        top_flange.projection.polygon.centroid.x
        - bottom_flange.projection.polygon.centroid.x
    )

    assert course_delta * candidate_delta > 0.0
    assert all(
        _is_explicit_outward_development(
            candidate, candidate.longitudinal_span - course.length
        )
        for candidate, course in (
            (bottom_flange, bottom_course),
            (top_flange, top_course),
        )
    )


@pytest.mark.parametrize(
    "member",
    ["2b1-cb-92", "2t1-cb-95", "h-9-cb-94", "h-9-cb-279"],
)
def test_flange_pair_never_contradicts_longitudinal_role_order(
    member: str,
) -> None:
    """A local development proof cannot reverse the H-view role relation."""

    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    best = solve_complete_box(source).best
    bottom_course, top_course = _outer_flange_courses(best.assignment, metadata)
    top_flange, bottom_flange = best.flange_candidates
    course_delta = top_course.longitudinal_center - bottom_course.longitudinal_center
    candidate_delta = (
        top_flange.projection.polygon.centroid.x
        - bottom_flange.projection.polygon.centroid.x
    )

    if abs(course_delta) <= 0.05:
        assert abs(candidate_delta) <= 0.05
    else:
        assert candidate_delta * course_delta > 0.0
    order_tolerance = (
        2.5
        * max(
            metadata.profile.value.web_thickness,
            metadata.profile.value.flange_thickness,
        )
        + 1.0
    )
    assert abs(candidate_delta - course_delta) <= order_tolerance
