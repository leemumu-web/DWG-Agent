from __future__ import annotations

from dataclasses import replace
from inspect import signature
from pathlib import Path

import pytest
from shapely.geometry import Polygon
from steel_dxf_split.box.assembly import (
    _apply_cross_view_web_total_spans,
    _assign_openings_to_pair,
    _cross_view_web_course_envelopes,
    _opening_representation_obligation,
)
from steel_dxf_split.box.flange_solver import enumerate_flange_outline_candidates
from steel_dxf_split.box.manufacturing_ir import (
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateRole,
    derive_weld_allowance_contract,
    rectangle_contour,
)
from steel_dxf_split.box.metadata import (
    BoxMetadata,
    BoxProfile,
    MetadataField,
    MetadataResolutionError,
    resolve_box_metadata,
)
from steel_dxf_split.box.openings import (
    InnerContourOpeningInventory,
    OpeningCandidateSearchSnapshot,
    OpeningOwnershipRoleCandidate,
    OpeningOwnershipScope,
    OpeningVisibility,
    ProjectedCircularOpening,
    ProjectedInnerContourOpening,
    lower_inner_contour_openings,
    project_circular_openings,
    project_inner_contour_openings,
)
from steel_dxf_split.box.projection_geometry import (
    ProjectedLoopSegment,
    ProjectedSourceLoop,
    ProjectionFaceCandidate,
    search_source_conserving_face_unions,
)
from steel_dxf_split.box.proofs import ProofStatus
from steel_dxf_split.box.role_hypotheses import enumerate_web_role_pairs
from steel_dxf_split.box.source_ir import (
    ObjectGroupIR,
    SourceDocumentIR,
    SourceEntityIR,
)
from steel_dxf_split.box.view_frame import PartViewIR, ViewFrame
from steel_dxf_split.box.view_solver import ViewAssignmentCandidate
from steel_dxf_split.box.web_solver import WebDerivation, WebOutlineCandidate
from steel_dxf_split.box.weld_allowance import stretch_outer_segments


def _title_text(
    handle: str,
    value: str,
    *,
    x: float,
    y: float,
) -> SourceEntityIR:
    return SourceEntityIR(
        source_id=f"insert:title/{handle}",
        group_id="insert:title",
        handle=handle,
        kind="TEXT",
        layer="DrawingSheet",
        linetype="BYLAYER",
        center=(x, y),
        text_raw=value,
        text_decoded=value,
        rotation=0.0,
    )


def _title_source(*entities: SourceEntityIR) -> SourceDocumentIR:
    return SourceDocumentIR(
        path=Path("synthetic-title-block.dxf"),
        dxf_version="AC1021",
        units=4,
        declared_codepage="ANSI_936",
        detected_encoding="utf-8",
        file_sha256="0" * 64,
        geometry_fingerprint="1" * 64,
        groups=(),
        entities=entities,
    )


def test_nominal_length_uses_length_column_when_quantity_is_numeric() -> None:
    source = _title_source(
        _title_text("01", "编号", x=0.0, y=100.0),
        _title_text("02", "截面", x=100.0, y=100.0),
        _title_text("03", "长度(mm)", x=200.0, y=100.0),
        _title_text("04", "材质", x=300.0, y=100.0),
        _title_text("05", "数量", x=400.0, y=100.0),
        _title_text("06", "比例", x=500.0, y=100.0),
        _title_text("11", "a1-1fd-cb-465", x=0.0, y=50.0),
        _title_text("12", "BOX400*250*25*25", x=100.0, y=50.0),
        _title_text("13", "2197", x=205.0, y=50.0),
        _title_text("14", "Q390C", x=300.0, y=50.0),
        _title_text("15", "2", x=405.0, y=50.0),
        _title_text("16", "1:60", x=500.0, y=50.0),
    )

    metadata = resolve_box_metadata(source)

    assert metadata.nominal_length.value == 2197.0
    assert metadata.nominal_length.source_id == "insert:title/13"


def test_box_boundary_allowance_uses_feature_free_middle_gap() -> None:
    evidence = FeatureEvidence(
        state=EvidenceState.DIRECT,
        source_ids=("synthetic",),
        rule_ids=("TEST",),
        proof_ids=("TEST",),
    )
    segments = rectangle_contour(0.0, 0.0, 3000.0, 100.0, evidence)
    contract = derive_weld_allowance_contract(segments)

    stretched = stretch_outer_segments(
        segments,
        contract,
        feature_x_extents=((100.0, 110.0), (2890.0, 2900.0)),
    )

    all_x = tuple(
        coordinate
        for segment in stretched
        for coordinate in (segment.start[0], segment.end[0])
    )
    assert max(all_x) - min(all_x) == pytest.approx(3005.0)
    assert {
        round(point[0], 6)
        for segment in stretched
        for point in (segment.start, segment.end)
        if 1496.0 <= point[0] <= 1506.0
    } == {1497.5, 1502.5}


def test_multiple_numbers_without_length_header_still_fail_closed() -> None:
    source = _title_source(
        _title_text("11", "a1-1fd-cb-465", x=0.0, y=50.0),
        _title_text("12", "BOX400*250*25*25", x=100.0, y=50.0),
        _title_text("13", "2197", x=205.0, y=50.0),
        _title_text("14", "Q390C", x=300.0, y=50.0),
        _title_text("15", "2", x=405.0, y=50.0),
        _title_text("16", "1:60", x=500.0, y=50.0),
    )

    with pytest.raises(MetadataResolutionError, match="nominal length"):
        resolve_box_metadata(source)


def test_nominal_length_uses_length_column_when_weight_replaces_quantity() -> None:
    # RDTSG-01-workflow-14 料表模板：零件编号/规格/长度/材质/重量（无“数量”列）。
    # 长度值与重量值都是纯数字，必须按“长度”表头 X 坐标消歧。
    source = _title_source(
        _title_text("01", "零件编号", x=4493.0, y=4084.0),
        _title_text("02", "规 格", x=4727.0, y=4080.0),
        _title_text("03", "长度", x=4954.0, y=4081.0),
        _title_text("04", "材质", x=5114.0, y=4081.0),
        _title_text("05", "重量", x=5282.0, y=4085.0),
        _title_text("11", "a1-cb-1", x=4483.0, y=4030.0),
        _title_text("12", "BOX800*800*40*40", x=4665.9, y=4028.0),
        _title_text("13", "4742", x=4946.5, y=4031.0),
        _title_text("14", "Q460CZ15", x=5071.0, y=4033.0),
        _title_text("15", "4509.0", x=5269.9, y=4036.0),
        _title_text("16", "1:10", x=5199.0, y=4040.0),
    )

    metadata = resolve_box_metadata(source)

    assert metadata.nominal_length.value == 4742.0
    assert metadata.nominal_length.source_id == "insert:title/13"


def _part_line(
    handle: str,
    start: tuple[float, float],
    end: tuple[float, float],
) -> SourceEntityIR:
    return SourceEntityIR(
        source_id=f"insert:H/{handle}",
        group_id="insert:H",
        handle=handle,
        kind="LINE",
        layer="Part",
        linetype="XKITLINE00",
        start=start,
        end=end,
    )


def _cross_view_assignment(
    *courses: tuple[str, str, tuple[float, float], tuple[float, float]],
) -> ViewAssignmentCandidate:
    entities = tuple(
        SourceEntityIR(
            source_id=f"insert:B/{handle}",
            group_id="insert:B",
            handle=handle,
            kind="LINE",
            layer="Part",
            linetype=linetype,
            start=start,
            end=end,
        )
        for handle, linetype, start, end in courses
    )
    b_view = PartViewIR(
        group_id="insert:B",
        block_name="B",
        entities=entities,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=-150.0,
            longitudinal_max=150.0,
            transverse_min=0.0,
            transverse_max=100.0,
        ),
    )
    h_view = replace(b_view, group_id="insert:H", block_name="H", entities=())
    return ViewAssignmentCandidate(
        h_view=h_view,
        b_view=b_view,
        h_span_error=0.0,
        b_span_error=0.0,
        score=0.0,
    )


def test_nested_visible_and_hidden_courses_prove_the_complete_web_span() -> None:
    assignment = _cross_view_assignment(
        ("visible", "XKITLINE00", (-100.0, 0.0), (100.0, 0.0)),
        ("hidden", "XKITLINE04", (-128.0, 30.0), (128.0, 30.0)),
    )

    envelopes = _cross_view_web_course_envelopes(
        assignment,
        face_separation_mm=30.0,
    )

    short = next(
        envelope
        for envelope in envelopes
        if envelope.reference_min_x == -100.0
    )
    assert (short.envelope_min_x, short.envelope_max_x) == (-128.0, 128.0)


def test_flange_search_uses_the_shared_face_union_budget() -> None:
    assert (
        "maximum_face_union_states"
        not in signature(enumerate_flange_outline_candidates).parameters
    )
    assert (
        signature(search_source_conserving_face_unions)
        .parameters["maximum_states"]
        .default
        == 50_000
    )


def test_nested_cross_view_courses_reject_an_unbounded_span_expansion() -> None:
    assignment = _cross_view_assignment(
        ("visible", "XKITLINE00", (-100.0, 0.0), (100.0, 0.0)),
        ("hidden", "XKITLINE04", (-180.0, 30.0), (180.0, 30.0)),
    )

    assert _cross_view_web_course_envelopes(
        assignment,
        face_separation_mm=30.0,
    ) == ()


def test_cross_view_span_application_is_independent_of_view_frame_origins() -> None:
    assignment = _cross_view_assignment(
        ("visible", "XKITLINE00", (-100.0, 0.0), (100.0, 0.0)),
        ("hidden", "XKITLINE04", (-128.0, 30.0), (128.0, 30.0)),
    )
    short = _web_candidate(
        "web:short",
        length=200.0,
        source_id="insert:H/short",
    )
    complete = _web_candidate(
        "web:complete",
        length=256.0,
        source_id="insert:H/complete",
    )

    adjusted = _apply_cross_view_web_total_spans(
        (short, complete),
        assignment,
        face_separation_mm=30.0,
    )

    assert adjusted[0].longitudinal_span == pytest.approx(256.0)
    assert adjusted[1] is complete


def _bolt_line(
    group_id: str,
    handle: str,
    start: tuple[float, float],
    end: tuple[float, float],
) -> SourceEntityIR:
    return SourceEntityIR(
        source_id=f"{group_id}/{handle}",
        group_id=group_id,
        handle=handle,
        kind="LINE",
        layer="Bolt",
        linetype="XKITLINE00",
        start=start,
        end=end,
    )


def _bolt_circle(
    group_id: str,
    handle: str,
    center: tuple[float, float],
    radius: float,
) -> SourceEntityIR:
    return SourceEntityIR(
        source_id=f"{group_id}/{handle}",
        group_id=group_id,
        handle=handle,
        kind="CIRCLE",
        layer="Bolt",
        linetype="XKITLINE00",
        center=center,
        radius=radius,
    )


def _source_with_bolt_groups(
    *groups: tuple[str, tuple[SourceEntityIR, ...]],
) -> SourceDocumentIR:
    return SourceDocumentIR(
        path=Path("synthetic-bolt-openings.dxf"),
        dxf_version="AC1021",
        units=4,
        declared_codepage="ANSI_936",
        detected_encoding="utf-8",
        file_sha256="0" * 64,
        geometry_fingerprint="1" * 64,
        groups=tuple(
            ObjectGroupIR(
                group_id=group_id,
                insert_handle=group_id.removeprefix("insert:"),
                block_name=group_id,
                insert_point=(0.0, 0.0),
                rotation=0.0,
                scale=(1.0, 1.0, 1.0),
                source_ids=tuple(entity.source_id for entity in entities),
                layers=("Bolt",),
            )
            for group_id, entities in groups
        ),
        entities=tuple(entity for _, entities in groups for entity in entities),
    )


def _opening_view(*part_entities: SourceEntityIR) -> PartViewIR:
    return PartViewIR(
        group_id="insert:H",
        block_name="H",
        entities=part_entities,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=1000.0,
            transverse_min=0.0,
            transverse_max=600.0,
        ),
    )


def _four_line_cross(
    group_id: str,
    *,
    center: tuple[float, float] = (400.0, 300.0),
    radius: float = 10.0,
) -> tuple[SourceEntityIR, ...]:
    x, y = center
    outer = radius * 2.0
    inner = radius * (2.0 / 3.0)
    return (
        _bolt_line(group_id, "left", (x - outer, y), (x - inner, y)),
        _bolt_line(group_id, "right", (x + inner, y), (x + outer, y)),
        _bolt_line(group_id, "bottom", (x, y - outer), (x, y - inner)),
        _bolt_line(group_id, "top", (x, y + inner), (x, y + outer)),
    )


def test_complete_bolt_center_cross_projects_hidden_face_circle() -> None:
    group_id = "insert:cross"
    source = _source_with_bolt_groups((group_id, _four_line_cross(group_id)))

    openings = project_circular_openings(source, _opening_view())

    assert len(openings) == 1
    assert openings[0].center == pytest.approx((400.0, 300.0))
    assert openings[0].radius_mm == pytest.approx(10.0)
    assert openings[0].visibility is OpeningVisibility.HIDDEN
    assert openings[0].source_ids == (
        "insert:cross/bottom",
        "insert:cross/left",
        "insert:cross/right",
        "insert:cross/top",
    )


def test_incomplete_bolt_center_cross_stays_fail_closed() -> None:
    group_id = "insert:partial-cross"
    entities = _four_line_cross(group_id)[:-1]
    source = _source_with_bolt_groups((group_id, entities))

    assert project_circular_openings(source, _opening_view()) == ()


def test_center_cross_matching_part_inner_loop_does_not_create_circle() -> None:
    group_id = "insert:cross"
    source = _source_with_bolt_groups((group_id, _four_line_cross(group_id)))
    center = (400.0, 300.0)
    radius = 10.0
    points = (
        (center[0] - radius, center[1]),
        (center[0], center[1] + radius),
        (center[0] + radius, center[1]),
        (center[0], center[1] - radius),
    )
    view = _opening_view(
        *(
            SourceEntityIR(
                source_id=f"insert:H/arc-{index}",
                group_id="insert:H",
                handle=f"arc-{index}",
                kind="ARC",
                layer="Part",
                linetype="XKITLINE04",
                center=center,
                radius=radius,
                start_angle=(180.0, 90.0, 0.0, 270.0)[index],
                end_angle=(90.0, 0.0, 270.0, 180.0)[index],
            )
            for index, _point in enumerate(points)
        )
    )

    assert project_circular_openings(source, view) == ()


def test_coincident_bolt_groups_preserve_independent_representation_count() -> None:
    first_id = "insert:near-face"
    second_id = "insert:far-face"
    source = _source_with_bolt_groups(
        (first_id, (_bolt_circle(first_id, "circle", (400.0, 300.0), 10.0),)),
        (
            second_id,
            (_bolt_circle(second_id, "circle", (400.02, 300.0), 10.0),),
        ),
    )

    openings = project_circular_openings(source, _opening_view())

    assert len(openings) == 1
    assert openings[0].representation_multiplicity == 2
    assert openings[0].source_ids == (
        "insert:far-face/circle",
        "insert:near-face/circle",
    )


def _outer_loop() -> ProjectedSourceLoop:
    points = (
        (-1700.0, -300.0),
        (1700.0, -300.0),
        (1700.0, 300.0),
        (-1700.0, 300.0),
    )
    segments = tuple(
        ProjectedLoopSegment(
            start=start,
            end=points[(index + 1) % len(points)],
            bulge=0.0,
            source_ids=(f"insert:H/outer-{index}",),
            visible_source_ids=(f"insert:H/outer-{index}",),
            hidden_source_ids=(),
            residual_mm=0.0,
        )
        for index, start in enumerate(points)
    )
    return ProjectedSourceLoop(
        polygon=Polygon(points),
        segments=segments,
        source_ids=tuple(
            f"insert:H/outer-{index}" for index in range(len(points))
        ),
        visible_source_ids=tuple(
            f"insert:H/outer-{index}" for index in range(len(points))
        ),
        hidden_source_ids=(),
        representation_multiplicity=1,
        residual_mm=0.0,
    )


def _lower_outer_loop_against(candidate_polygon: Polygon):
    outer = _outer_loop()
    candidate = ProjectionFaceCandidate(
        polygon=candidate_polygon,
        boundary_source_ids=("insert:H/inner-bottom", "insert:H/inner-top"),
        vertex_source_ids=("insert:H/inner-bottom", "insert:H/inner-top"),
        source_conserved=True,
        grid_size_mm=0.001,
        rule_ids=("BOX.WEB.INNER_COURSE_BAND",),
    )
    entities = (
        _part_line("inner-bottom", (-1700.0, -278.0), (1700.0, -278.0)),
        _part_line("inner-top", (-1700.0, 278.0), (1700.0, 278.0)),
        *(
            _part_line(f"outer-{index}", segment.start, segment.end)
            for index, segment in enumerate(outer.segments)
        ),
    )
    view = PartViewIR(
        group_id="insert:H",
        block_name="H",
        entities=entities,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=-1700.0,
            longitudinal_max=1700.0,
            transverse_min=-300.0,
            transverse_max=300.0,
        ),
    )
    search = OpeningCandidateSearchSnapshot.capture(
        view=view,
        candidates=(candidate,),
        enumerator_id="box.web_outline_candidates.v1",
        enumerator_exhausted=True,
    )
    roles = (
        OpeningOwnershipRoleCandidate(
            candidate_id="candidate:web-left",
            role=PhysicalPlateRole.WEB_LEFT,
            projection=candidate,
        ),
        OpeningOwnershipRoleCandidate(
            candidate_id="candidate:web-right",
            role=PhysicalPlateRole.WEB_RIGHT,
            projection=candidate,
        ),
    )
    scope = OpeningOwnershipScope.from_candidate_search(
        hypothesis_id="hypothesis:outer-envelope",
        view=view,
        candidate_search=search,
        role_candidates=roles,
    )
    inventory = InnerContourOpeningInventory(
        openings=(
            ProjectedInnerContourOpening(
                loop=outer,
                visibility=OpeningVisibility.VISIBLE,
                view_group_id=view.group_id,
            ),
        ),
        rejections=(),
    )

    return lower_inner_contour_openings(
        roles[0],
        inventory,
        ownership_scope=scope,
    )


def test_outer_section_envelope_is_course_context_not_an_opening_conflict() -> None:
    result = _lower_outer_loop_against(
        Polygon(
            (
                (-1700.0, -278.0),
                (1700.0, -278.0),
                (1700.0, 278.0),
                (-1700.0, 278.0),
            )
        )
    )

    assert result.contours == ()
    assert tuple(rejection.reason for rejection in result.rejections) == (
        "candidate_course_context",
    )


def test_non_rectangular_candidate_remains_a_boundary_conflict() -> None:
    result = _lower_outer_loop_against(
        Polygon(
            (
                (-1700.0, -278.0),
                (0.0, -278.0),
                (0.0, -200.0),
                (1700.0, -200.0),
                (1700.0, 200.0),
                (0.0, 200.0),
                (0.0, 278.0),
                (-1700.0, 278.0),
            )
        )
    )

    assert result.contours == ()
    assert tuple(rejection.reason for rejection in result.rejections) == (
        "candidate_boundary_conflict",
    )


def _metadata_field(value, source_id: str, raw: str):
    return MetadataField(
        value=value,
        source_id=source_id,
        raw_text=raw,
        normalized_text=raw,
    )


def _box_metadata() -> BoxMetadata:
    return BoxMetadata(
        member_mark=_metadata_field("6b5-cb-4", "title/member", "6b5-cb-4"),
        profile=_metadata_field(
            BoxProfile(600.0, 600.0, 20.0, 20.0),
            "title/profile",
            "BOX600*600*20*20",
        ),
        nominal_length=_metadata_field(4405.0, "title/length", "4405"),
        material=_metadata_field("Q355C", "title/material", "Q355C"),
        scale_denominator=_metadata_field(20, "title/scale", "1:20"),
        title_group_id="title",
    )


def _web_candidate(
    candidate_id: str,
    *,
    length: float,
    source_id: str,
    derivations: tuple[WebDerivation, ...] = (WebDerivation.SOURCE_FACE_UNION,),
) -> WebOutlineCandidate:
    evidence = FeatureEvidence(
        state=EvidenceState.DIRECT,
        source_ids=(source_id,),
        rule_ids=("BOX.WEB.SOURCE_FACE_UNION",),
        proof_ids=("test",),
        description="synthetic source-conserved web face",
    )
    polygon = Polygon(
        ((0.0, 0.0), (length, 0.0), (length, 560.0), (0.0, 560.0))
    )
    return WebOutlineCandidate(
        candidate_id=candidate_id,
        contour=rectangle_contour(0.0, 0.0, length, 560.0, evidence),
        projection=ProjectionFaceCandidate(
            polygon=polygon,
            boundary_source_ids=(source_id,),
            vertex_source_ids=(source_id,),
            source_conserved=True,
            grid_size_mm=0.001,
            rule_ids=("BOX.PROJECTION.SOURCE_FACE_UNION",),
        ),
        derivations=derivations,
        source_ids=(source_id,),
    )


def _web_view(*candidates: WebOutlineCandidate) -> PartViewIR:
    lengths = tuple(candidate.projection.polygon.bounds[2] for candidate in candidates)
    entities = tuple(
        SourceEntityIR(
            source_id=candidate.source_ids[0],
            group_id="insert:H",
            handle=candidate.candidate_id,
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(0.0, 0.0),
            end=(length, 0.0),
        )
        for candidate, length in zip(candidates, lengths, strict=True)
    )
    return PartViewIR(
        group_id="insert:H",
        block_name="H",
        entities=entities,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=max(lengths),
            transverse_min=0.0,
            transverse_max=600.0,
        ),
    )


def _opening_pair_buckets(
    opening: ProjectedCircularOpening,
) -> tuple[tuple[ProjectedCircularOpening, ...], ...]:
    first = _web_candidate(
        "web:first",
        length=4405.0,
        source_id="insert:H/first",
    )
    second = _web_candidate(
        "web:second",
        length=4405.0,
        source_id="insert:H/second",
    )
    return _assign_openings_to_pair(
        (first, second),
        (opening,),
        _web_view(first, second),
        duplicate_legacy_bolt_openings=True,
    )


def test_single_visible_bolt_circle_belongs_only_to_near_face_plate() -> None:
    opening = ProjectedCircularOpening(
        center=(400.0, 300.0),
        radius_mm=10.0,
        source_ids=("insert:near/circle",),
        cluster_residual_mm=0.0,
        visibility=OpeningVisibility.VISIBLE,
        representation_multiplicity=1,
        view_group_id="insert:H",
    )

    buckets = _opening_pair_buckets(opening)

    assert tuple(len(bucket) for bucket in buckets) == (1, 0)


def test_single_hidden_bolt_cross_belongs_only_to_far_face_plate() -> None:
    opening = ProjectedCircularOpening(
        center=(400.0, 300.0),
        radius_mm=10.0,
        source_ids=(
            "insert:far/bottom",
            "insert:far/left",
            "insert:far/right",
            "insert:far/top",
        ),
        cluster_residual_mm=0.0,
        visibility=OpeningVisibility.HIDDEN,
        representation_multiplicity=1,
        view_group_id="insert:H",
    )

    buckets = _opening_pair_buckets(opening)

    assert tuple(len(bucket) for bucket in buckets) == (0, 1)


def test_two_independent_coincident_bolt_groups_prove_shared_pair_hole() -> None:
    opening = ProjectedCircularOpening(
        center=(400.0, 300.0),
        radius_mm=10.0,
        source_ids=("insert:near/circle", "insert:far/circle"),
        cluster_residual_mm=0.02,
        visibility=OpeningVisibility.VISIBLE,
        representation_multiplicity=2,
        view_group_id="insert:H",
    )

    buckets = _opening_pair_buckets(opening)

    assert tuple(len(bucket) for bucket in buckets) == (1, 1)


def test_unpaired_hidden_bolt_representation_requires_review() -> None:
    hidden = ProjectedCircularOpening(
        center=(400.0, 300.0),
        radius_mm=10.0,
        source_ids=(
            "insert:far/bottom",
            "insert:far/left",
            "insert:far/right",
            "insert:far/top",
        ),
        cluster_residual_mm=0.0,
        visibility=OpeningVisibility.HIDDEN,
        representation_multiplicity=1,
        view_group_id="insert:B",
    )

    obligation = _opening_representation_obligation((hidden,))

    assert obligation.status is ProofStatus.MISSING
    assert obligation.critical is True
    assert obligation.diagnostic_code == "BOX.OPENING.UNPAIRED_HIDDEN_REPRESENTATION"


def test_visible_only_bolt_representation_needs_no_pair_proof() -> None:
    visible = ProjectedCircularOpening(
        center=(300.0, 300.0),
        radius_mm=10.0,
        source_ids=("insert:near/circle",),
        cluster_residual_mm=0.0,
        visibility=OpeningVisibility.VISIBLE,
        representation_multiplicity=1,
        view_group_id="insert:B",
    )

    obligation = _opening_representation_obligation((visible,))

    assert obligation.status is ProofStatus.NOT_APPLICABLE
    assert obligation.diagnostic_code is None


def test_visible_and_hidden_bolt_representations_complete_the_pair_evidence() -> None:
    visible = ProjectedCircularOpening(
        center=(300.0, 300.0),
        radius_mm=10.0,
        source_ids=("insert:near/circle",),
        cluster_residual_mm=0.0,
        visibility=OpeningVisibility.VISIBLE,
        representation_multiplicity=1,
        view_group_id="insert:B",
    )
    hidden = replace(
        visible,
        center=(500.0, 300.0),
        source_ids=(
            "insert:far/bottom",
            "insert:far/left",
            "insert:far/right",
            "insert:far/top",
        ),
        visibility=OpeningVisibility.HIDDEN,
    )

    obligation = _opening_representation_obligation((visible, hidden))

    assert obligation.status is ProofStatus.PASS
    assert obligation.diagnostic_code is None


def test_visible_bolt_in_other_view_proves_hidden_representation_convention() -> None:
    visible = ProjectedCircularOpening(
        center=(300.0, 300.0),
        radius_mm=100.0,
        source_ids=("insert:web/circle",),
        cluster_residual_mm=0.0,
        visibility=OpeningVisibility.VISIBLE,
        representation_multiplicity=1,
        view_group_id="insert:H",
    )
    hidden = replace(
        visible,
        center=(500.0, 300.0),
        radius_mm=10.0,
        source_ids=(
            "insert:flange/bottom",
            "insert:flange/left",
            "insert:flange/right",
            "insert:flange/top",
        ),
        visibility=OpeningVisibility.HIDDEN,
        view_group_id="insert:B",
    )

    obligation = _opening_representation_obligation((visible, hidden))

    assert obligation.status is ProofStatus.PASS
    assert obligation.diagnostic_code is None


def test_web_roles_reject_a_near_zero_longitudinal_face_partition() -> None:
    full = _web_candidate("web:full", length=4405.044, source_id="insert:H/full")
    sliver = _web_candidate("web:sliver", length=0.084, source_id="insert:H/sliver")

    result = enumerate_web_role_pairs(
        (full, sliver),
        (),
        _web_view(full, sliver),
        _box_metadata(),
        part_arc_evidence=False,
    )

    assert tuple(
        tuple(candidate.candidate_id for candidate in pair)
        for pair in result.pairs
    ) == (("web:full", "web:full"),)


def test_web_roles_keep_a_source_course_above_drafting_sliver_scale() -> None:
    full = _web_candidate("web:full", length=4405.044, source_id="insert:H/full")
    shorter = _web_candidate(
        "web:shorter",
        length=500.0,
        source_id="insert:H/shorter",
    )

    result = enumerate_web_role_pairs(
        (full, shorter),
        (),
        _web_view(full, shorter),
        _box_metadata(),
        part_arc_evidence=False,
    )

    assert tuple(
        tuple(candidate.candidate_id for candidate in pair)
        for pair in result.pairs
    ) == (("web:full", "web:shorter"),)


def test_web_roles_add_nominal_inner_band_reuse_for_chamfer_scale_course() -> None:
    full = _web_candidate(
        "web:full",
        length=4405.0,
        source_id="insert:H/full",
        derivations=(WebDerivation.INNER_COURSE_BAND,),
    )
    chamfer_course = _web_candidate(
        "web:chamfer-course",
        length=4385.0,
        source_id="insert:H/chamfer-course",
    )

    result = enumerate_web_role_pairs(
        (full, chamfer_course),
        (),
        _web_view(full, chamfer_course),
        _box_metadata(),
        part_arc_evidence=False,
    )

    assert {
        tuple(candidate.candidate_id for candidate in pair)
        for pair in result.pairs
    } == {
        ("web:full", "web:full"),
        ("web:full", "web:chamfer-course"),
    }


def test_web_roles_reject_a_longitudinal_partition_shorter_than_wall() -> None:
    full = _web_candidate("web:full", length=4405.0, source_id="insert:H/full")
    partition = _web_candidate(
        "web:partition",
        length=19.0,
        source_id="insert:H/partition",
    )

    result = enumerate_web_role_pairs(
        (full, partition),
        (),
        _web_view(full, partition),
        _box_metadata(),
        part_arc_evidence=False,
    )

    assert tuple(
        tuple(candidate.candidate_id for candidate in pair)
        for pair in result.pairs
    ) == (("web:full", "web:full"),)


def test_inner_loop_that_is_same_bolt_circle_is_not_projected() -> None:
    # RDTSG a1-cb-* 特殊结构：同一圆孔被输出为 Part 图层的 ARC+切线环
    # （被当作“非圆形内轮廓”）与 Bolt 图层的单个 CIRCLE。内轮廓必须让位
    # 于圆孔，否则材料被挖掉后圆孔校验失败。
    import math

    center = (1135.73, 0.0)
    radius = 100.0
    n = 8
    points = [
        (
            center[0] + radius * math.cos(2.0 * math.pi * i / n),
            center[1] + radius * math.sin(2.0 * math.pi * i / n),
        )
        for i in range(n)
    ]
    loop_entities = tuple(
        _part_line(
            f"slot-{index}",
            points[index],
            points[(index + 1) % n],
        )
        for index in range(n)
    )
    view = PartViewIR(
        group_id="insert:H",
        block_name="H",
        entities=loop_entities,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=1400.0,
            transverse_min=-600.0,
            transverse_max=600.0,
        ),
    )
    bolt = ProjectedCircularOpening(
        center=center,
        radius_mm=radius,
        source_ids=("insert:bolt/1A",),
        cluster_residual_mm=0.0,
    )

    without_filter = project_inner_contour_openings(view)
    with_filter = project_inner_contour_openings(
        view,
        circular_openings=(bolt,),
    )

    assert len(without_filter.openings) == 1
    assert len(with_filter.openings) == 0
