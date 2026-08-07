from __future__ import annotations

from pathlib import Path

import pytest
from shapely.geometry import Polygon

from steel_dxf_split.box.manufacturing_ir import (
    EvidenceState,
    FeatureEvidence,
    PhysicalPlateRole,
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
    ProjectedInnerContourOpening,
    lower_inner_contour_openings,
)
from steel_dxf_split.box.projection_geometry import (
    ProjectedLoopSegment,
    ProjectedSourceLoop,
    ProjectionFaceCandidate,
)
from steel_dxf_split.box.role_hypotheses import enumerate_web_role_pairs
from steel_dxf_split.box.source_ir import SourceDocumentIR, SourceEntityIR
from steel_dxf_split.box.view_frame import PartViewIR, ViewFrame
from steel_dxf_split.box.web_solver import WebDerivation, WebOutlineCandidate


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
        derivations=(WebDerivation.SOURCE_FACE_UNION,),
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
