from __future__ import annotations

from shapely.geometry import Polygon
from steel_dxf_split.box.assembly import _flange_course_authority_conflicts
from steel_dxf_split.box.flange_solver import (
    FlangeCandidateSearchResult,
    FlangeDerivation,
    FlangeOutlineCandidate,
)
from steel_dxf_split.box.manufacturing_ir import (
    EvidenceState,
    FeatureEvidence,
    rectangle_contour,
)
from steel_dxf_split.box.metadata import (
    BoxMetadata,
    BoxProfile,
    MetadataField,
)
from steel_dxf_split.box.projection_geometry import (
    CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,
    ProjectionFaceCandidate,
)
from steel_dxf_split.box.role_hypotheses import (
    FlangeCourseEvidence,
    enumerate_straight_flange_role_pairs,
    role_aligned_exact_flange_pair,
)
from steel_dxf_split.box.source_ir import SourceEntityIR
from steel_dxf_split.box.view_frame import PartViewIR, ViewFrame
from steel_dxf_split.box.view_solver import ViewAssignmentCandidate


def _field(value, source_id: str, raw: str):
    return MetadataField(
        value=value,
        source_id=source_id,
        raw_text=raw,
        normalized_text=raw,
    )


def _metadata() -> BoxMetadata:
    return BoxMetadata(
        member_mark=_field("fixture", "title/member", "fixture"),
        profile=_field(
            BoxProfile(700.0, 400.0, 30.0, 30.0),
            "title/profile",
            "BOX700*400*30*30",
        ),
        nominal_length=_field(738.0, "title/length", "738"),
        material=_field("Q355C", "title/material", "Q355C"),
        scale_denominator=_field(20, "title/scale", "1:20"),
        title_group_id="title",
    )


def _candidate(
    candidate_id: str,
    *,
    length: float,
    center_x: float,
    derivations: tuple[FlangeDerivation, ...],
    connected_source_face: bool = False,
) -> FlangeOutlineCandidate:
    evidence = FeatureEvidence(
        state=EvidenceState.DIRECT,
        source_ids=(f"source:{candidate_id}",),
        rule_ids=("TEST.SOURCE.CONSERVED",),
        proof_ids=("TEST.PROOF",),
    )
    min_x = center_x - length / 2.0
    max_x = center_x + length / 2.0
    polygon = Polygon(
        ((min_x, 0.0), (max_x, 0.0), (max_x, 400.0), (min_x, 400.0))
    )
    rules = (
        (CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,)
        if connected_source_face
        else (
            "BOX.FLANGE.COURSE_STATION_DEVELOPMENT",
            "BOX.FLANGE.PAIRED_CAPS.EXTENDED_INNER_COUNT_1",
            "BOX.FLANGE.PARALLEL_COURSE_OFFSET",
        )
    )
    return FlangeOutlineCandidate(
        candidate_id=candidate_id,
        contour=rectangle_contour(min_x, 0.0, max_x, 400.0, evidence),
        projection=ProjectionFaceCandidate(
            polygon=polygon,
            boundary_source_ids=(f"source:{candidate_id}",),
            vertex_source_ids=(f"source:{candidate_id}",),
            source_conserved=True,
            grid_size_mm=0.001,
            rule_ids=rules,
        ),
        derivations=derivations,
        source_ids=(f"source:{candidate_id}",),
        rule_ids=rules,
        support_source_sets=((f"source:{candidate_id}",),),
    )


def _courses(
    *,
    bottom_length: float,
    top_length: float,
) -> tuple[FlangeCourseEvidence, FlangeCourseEvidence]:
    return (
        FlangeCourseEvidence("bottom", bottom_length, 0.0, ("course:bottom",)),
        FlangeCourseEvidence("top", top_length, 18.0, ("course:top",)),
    )


def test_exact_role_course_outranks_positive_offset_development() -> None:
    """A derived +35 mm top flange must not displace its exact H-view course."""

    bottom = _candidate(
        "bottom-exact",
        length=738.109474,
        center_x=0.0,
        derivations=(FlangeDerivation.SOURCE_FACE_UNION,),
        connected_source_face=True,
    )
    top_exact = _candidate(
        "top-exact",
        length=701.633263,
        center_x=18.0,
        derivations=(
            FlangeDerivation.COURSE_STATION_RECTANGLE,
            FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,
            FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT,
        ),
    )
    top_offset = _candidate(
        "top-offset",
        length=736.476211,
        center_x=18.0,
        derivations=(
            FlangeDerivation.COURSE_STATION_RECTANGLE,
            FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,
            FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT,
        ),
    )

    result = enumerate_straight_flange_role_pairs(
        (bottom, top_exact, top_offset),
        _courses(bottom_length=738.109474, top_length=701.633263),
        _metadata(),
    )

    assert result.pairs
    assert {pair[0].candidate_id for pair in result.pairs} == {"top-exact"}


def test_cross_view_origin_does_not_displace_exact_role_courses() -> None:
    """H/B local origins must not override a proven top/bottom course relation."""

    bottom_exact = _candidate(
        "bottom-exact-shifted",
        length=738.109474,
        center_x=110.315088,
        derivations=(FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,),
    )
    top_exact = _candidate(
        "top-exact-shifted",
        length=701.633263,
        center_x=128.553194,
        derivations=(
            FlangeDerivation.COURSE_STATION_RECTANGLE,
            FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,
            FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT,
        ),
    )
    top_positive_offset = _candidate(
        "top-positive-offset",
        length=736.476211,
        center_x=111.131719,
        derivations=(
            FlangeDerivation.COURSE_STATION_RECTANGLE,
            FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,
            FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT,
        ),
    )
    courses = (
        FlangeCourseEvidence(
            "bottom",
            738.109474,
            -9.527369,
            ("course:bottom",),
        ),
        FlangeCourseEvidence(
            "top",
            701.633263,
            8.710737,
            ("course:top",),
        ),
    )

    result = enumerate_straight_flange_role_pairs(
        (bottom_exact, top_exact, top_positive_offset),
        courses,
        _metadata(),
    )

    assert {
        (pair[0].candidate_id, pair[1].candidate_id)
        for pair in result.pairs
    } == {("top-exact-shifted", "bottom-exact-shifted")}


def test_symmetric_courses_do_not_claim_shifted_cross_view_role_authority() -> None:
    """Coincident H-view roles cannot anchor a shifted B-view exact pair."""

    shifted = _candidate(
        "shifted-symmetric-course",
        length=13_092.126,
        center_x=3_627.368,
        derivations=(
            FlangeDerivation.COURSE_STATION_RECTANGLE,
            FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,
            FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT,
        ),
    )
    course = FlangeCourseEvidence(
        "symmetric",
        13_092.126,
        2_172.688,
        ("course:symmetric",),
    )

    assert not role_aligned_exact_flange_pair(
        (shifted, shifted),
        bottom_course=course,
        top_course=course,
    )


def test_one_role_exact_geometry_cannot_be_reused_for_both_courses() -> None:
    """A long exact top flange cannot also stand in for a shorter bottom course."""

    top_exact = _candidate(
        "top-long",
        length=717.069,
        center_x=18.0,
        derivations=(FlangeDerivation.SOURCE_FACE_UNION,),
        connected_source_face=True,
    )
    bottom_exact = _candidate(
        "bottom-short",
        length=683.430,
        center_x=0.0,
        derivations=(
            FlangeDerivation.COURSE_STATION_RECTANGLE,
            FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,
        ),
    )

    result = enumerate_straight_flange_role_pairs(
        (top_exact, bottom_exact),
        _courses(bottom_length=683.430, top_length=717.069),
        _metadata(),
    )

    assert result.pairs
    assert {
        (pair[0].candidate_id, pair[1].candidate_id)
        for pair in result.pairs
    } == {("top-long", "bottom-short")}


def test_misaligned_exact_courses_do_not_displace_proven_development() -> None:
    """Length equality alone is not a role binding across the H/B views."""

    bottom = _candidate(
        "bottom-exact",
        length=100.0,
        center_x=0.0,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    top_exact_but_misaligned = _candidate(
        "top-exact-misaligned",
        length=80.0,
        center_x=30.0,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    top_developed = _candidate(
        "top-developed",
        length=90.0,
        center_x=20.0,
        derivations=(FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,),
    )

    result = enumerate_straight_flange_role_pairs(
        (bottom, top_exact_but_misaligned, top_developed),
        _courses(bottom_length=100.0, top_length=80.0),
        _metadata(),
    )

    assert result.pairs
    assert {pair[0].candidate_id for pair in result.pairs} == {"top-developed"}


def test_translated_exact_courses_do_not_displace_proven_development() -> None:
    """Relative spacing alone is not enough when the whole pair is translated."""

    bottom = _candidate(
        "bottom-translated-exact",
        length=100.0,
        center_x=800.0,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    top_translated_exact = _candidate(
        "top-translated-exact",
        length=80.0,
        center_x=820.0,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    top_developed = _candidate(
        "top-developed",
        length=120.0,
        center_x=20.0,
        derivations=(FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,),
    )

    result = enumerate_straight_flange_role_pairs(
        (bottom, top_translated_exact, top_developed),
        _courses(bottom_length=100.0, top_length=80.0),
        _metadata(),
    )

    assert result.pairs
    assert {pair[0].candidate_id for pair in result.pairs} == {"top-developed"}


def test_complete_shared_flange_can_represent_two_physical_roles() -> None:
    """One complete plate outline may carry quantity two when both roles share it."""

    shared = _candidate(
        "shared-complete-flange",
        length=100.0,
        center_x=0.0,
        derivations=(FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,),
    )

    result = enumerate_straight_flange_role_pairs(
        (shared,),
        _courses(bottom_length=100.0, top_length=80.0),
        _metadata(),
    )

    assert result.pairs == ((shared, shared),)


def test_drafting_duplicate_flange_meanings_collapse_to_stable_geometry() -> None:
    """Drafting-level duplicates must not leave final choice to candidate IDs."""

    bottom_reference = _candidate(
        "bottom-reference",
        length=1757.931,
        center_x=878.966,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    bottom_duplicate = _candidate(
        "bottom-duplicate",
        length=1757.943,
        center_x=878.972,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    top_reference = _candidate(
        "top-reference",
        length=1283.735,
        center_x=641.868,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    top_duplicate = _candidate(
        "top-duplicate",
        length=1283.747,
        center_x=641.873,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )

    result = enumerate_straight_flange_role_pairs(
        (
            bottom_duplicate,
            top_duplicate,
            top_reference,
            bottom_reference,
        ),
        (
            FlangeCourseEvidence("bottom", 1757.931, 878.966, ("course:bottom",)),
            FlangeCourseEvidence("top", 1283.735, 641.868, ("course:top",)),
        ),
        _metadata(),
    )

    assert {
        (pair[0].candidate_id, pair[1].candidate_id)
        for pair in result.pairs
    } == {("top-reference", "bottom-reference")}


def test_same_flange_meaning_keeps_role_aligned_authority_winner() -> None:
    """Representative selection must not undo a proven top/bottom role offset."""

    shared = _candidate(
        "shared-shorter-fit",
        length=100.0,
        center_x=-0.047,
        derivations=(FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,),
    )
    bottom_aligned = _candidate(
        "bottom-role-aligned",
        length=99.999,
        center_x=0.0,
        derivations=(FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,),
    )
    top_aligned = _candidate(
        "top-role-aligned",
        length=100.001,
        center_x=-0.094,
        derivations=(FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,),
    )
    courses = (
        FlangeCourseEvidence("bottom", 100.0, 0.0, ("course:bottom",)),
        FlangeCourseEvidence("top", 100.0, -0.094, ("course:top",)),
    )

    result = enumerate_straight_flange_role_pairs(
        (shared, bottom_aligned, top_aligned),
        courses,
        _metadata(),
    )

    assert len(result.pairs) == 1
    selected = result.pairs[0]
    assert selected[0].candidate_id != selected[1].candidate_id
    assert role_aligned_exact_flange_pair(
        selected,
        bottom_course=courses[0],
        top_course=courses[1],
    )


def test_proof_flags_displaced_exact_role_course_without_maximal_face_tag() -> None:
    """The final proof must catch an offset winner even for course-derived exact data."""

    top_exact = _candidate(
        "top-exact",
        length=701.633263,
        center_x=18.0,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    top_offset = _candidate(
        "top-offset",
        length=736.476211,
        center_x=18.0,
        derivations=(FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT,),
    )
    bottom = _candidate(
        "bottom-exact",
        length=738.109474,
        center_x=0.0,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    h_view = PartViewIR(
        group_id="h",
        block_name="H",
        entities=(
            SourceEntityIR(
                source_id="course:bottom",
                group_id="h",
                handle="1",
                kind="LINE",
                layer="Part",
                linetype="CONTINUOUS",
                start=(-369.054737, 0.0),
                end=(369.054737, 0.0),
            ),
            SourceEntityIR(
                source_id="course:top",
                group_id="h",
                handle="2",
                kind="LINE",
                layer="Part",
                linetype="CONTINUOUS",
                start=(-332.8166315, 700.0),
                end=(368.8166315, 700.0),
            ),
        ),
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=-400.0,
            longitudinal_max=400.0,
            transverse_min=0.0,
            transverse_max=700.0,
        ),
    )
    assignment = ViewAssignmentCandidate(
        h_view=h_view,
        b_view=h_view,
        h_span_error=0.0,
        b_span_error=0.0,
        score=0.0,
    )
    search = FlangeCandidateSearchResult(
        candidates=(top_exact, top_offset, bottom),
        direct_face_search_pruned=False,
        direct_face_search_complete=True,
        diagnostics=(),
    )

    conflicts = _flange_course_authority_conflicts(
        assignment,
        _metadata(),
        search,
        (top_offset, bottom),
    )

    assert tuple(candidate.candidate_id for candidate in conflicts) == ("top-offset",)


def test_proof_ignores_length_only_exact_candidate_without_role_alignment() -> None:
    """The proof gate must not promote an unrelated equal-length B-view face."""

    top_exact_but_misaligned = _candidate(
        "top-exact-misaligned",
        length=701.633263,
        center_x=28.0,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    top_developed = _candidate(
        "top-developed",
        length=719.633263,
        center_x=18.0,
        derivations=(FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,),
    )
    bottom = _candidate(
        "bottom-exact",
        length=738.109474,
        center_x=0.0,
        derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
    )
    h_view = PartViewIR(
        group_id="h",
        block_name="H",
        entities=(
            SourceEntityIR(
                source_id="course:bottom",
                group_id="h",
                handle="1",
                kind="LINE",
                layer="Part",
                linetype="CONTINUOUS",
                start=(-369.054737, 0.0),
                end=(369.054737, 0.0),
            ),
            SourceEntityIR(
                source_id="course:top",
                group_id="h",
                handle="2",
                kind="LINE",
                layer="Part",
                linetype="CONTINUOUS",
                start=(-332.8166315, 700.0),
                end=(368.8166315, 700.0),
            ),
        ),
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=-400.0,
            longitudinal_max=400.0,
            transverse_min=0.0,
            transverse_max=700.0,
        ),
    )
    assignment = ViewAssignmentCandidate(
        h_view=h_view,
        b_view=h_view,
        h_span_error=0.0,
        b_span_error=0.0,
        score=0.0,
    )
    search = FlangeCandidateSearchResult(
        candidates=(top_exact_but_misaligned, top_developed, bottom),
        direct_face_search_pruned=False,
        direct_face_search_complete=True,
        diagnostics=(),
    )

    conflicts = _flange_course_authority_conflicts(
        assignment,
        _metadata(),
        search,
        (top_developed, bottom),
    )

    assert conflicts == ()
