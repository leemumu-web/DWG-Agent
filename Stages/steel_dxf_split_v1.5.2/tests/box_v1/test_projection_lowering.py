from __future__ import annotations

from math import cos, radians, sin
import pytest
from shapely.geometry import Polygon

from steel_dxf_split.box.manufacturing_ir import contour_polygon
from steel_dxf_split.box.metadata import BoxProfile, resolve_box_metadata
from steel_dxf_split.box.projection_geometry import (
    ProjectionFaceCandidate,
    enumerate_connected_inner_course_cycles,
)
from steel_dxf_split.box.projection_lowering import lower_projection_face_to_contour
from steel_dxf_split.box.source_ir import SourceEntityIR, build_source_ir
from steel_dxf_split.box.view_frame import ViewFrame, build_part_views
from steel_dxf_split.box.view_solver import enumerate_view_assignments
from tests.box_v1.paths import INPUTS


def test_projection_fillet_lowering_preserves_a_real_adjacent_cut_edge() -> None:
    radius = 10.0
    arc_points = tuple(
        (
            10.0 + radius * cos(radians(angle)),
            40.0 + radius * sin(radians(angle)),
        )
        for angle in range(90, 181, 3)
    )
    polygon = Polygon(
        (
            (0.0, 0.0),
            (100.0, 0.0),
            (100.0, 50.0),
            (18.0, 50.0),
            *arc_points,
            (0.0, 0.0),
        )
    )
    candidate = ProjectionFaceCandidate(
        polygon=polygon,
        boundary_source_ids=("top-cut", "fillet"),
        vertex_source_ids=("top-cut", "fillet"),
        source_conserved=True,
        grid_size_mm=0.001,
    )
    entities = (
        SourceEntityIR(
            source_id="top-cut",
            group_id="synthetic",
            handle="1",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(18.0, 50.0),
            end=(10.0, 50.0),
        ),
        SourceEntityIR(
            source_id="fillet",
            group_id="synthetic",
            handle="2",
            kind="ARC",
            layer="Part",
            linetype="XKITLINE04",
            center=(10.0, 40.0),
            radius=radius,
            start_angle=90.0,
            end_angle=180.0,
        ),
    )
    frame = ViewFrame(
        origin=(0.0, 0.0),
        longitudinal_axis=(1.0, 0.0),
        transverse_axis=(0.0, 1.0),
        longitudinal_min=0.0,
        longitudinal_max=100.0,
        transverse_min=0.0,
        transverse_max=50.0,
    )

    contour = lower_projection_face_to_contour(
        candidate,
        entities,
        frame,
        BoxProfile(
            height=100.0,
            width=100.0,
            web_thickness=20.0,
            flange_thickness=20.0,
        ),
    )

    assert any(
        segment.start == pytest.approx((18.0, 50.0))
        and segment.end == pytest.approx((10.0, 50.0))
        for segment in contour
    )


def test_exact_tekla_projection_arcs_lower_to_manufacturing_bulges() -> None:
    source = build_source_ir(INPUTS / "h-9-cb-73_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[0]
    projection = enumerate_connected_inner_course_cycles(
        assignment.h_view.entities,
        assignment.h_view.frame,
        target_transverse_mm=metadata.profile.value.web_clear_width,
    )[0]

    contour = lower_projection_face_to_contour(
        projection,
        assignment.h_view.entities,
        assignment.h_view.frame,
        metadata.profile.value,
    )

    assert contour_polygon(contour).is_valid
    assert any(
        abs(segment.bulge) > 1e-8
        and "BOX.LOWER.PROJECTION_FILLET_TO_SOURCE_ARC" in segment.evidence.rule_ids
        for segment in contour
    )
