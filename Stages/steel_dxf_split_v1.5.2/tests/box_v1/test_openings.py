from __future__ import annotations

from pathlib import Path

from shapely.geometry import Point

from steel_dxf_split.box.manufacturing_ir import contour_polygon
from steel_dxf_split.box.metadata import resolve_box_metadata
from steel_dxf_split.box.openings import (
    OpeningVisibility,
    lower_circular_openings,
    project_circular_openings,
    project_part_arc_openings,
)
from steel_dxf_split.box.source_ir import (
    SourceDocumentIR,
    SourceEntityIR,
    build_source_ir,
)
from steel_dxf_split.box.view_frame import (
    PartViewIR,
    ViewFrame,
    build_part_views,
)
from steel_dxf_split.box.view_solver import enumerate_view_assignments
from steel_dxf_split.box.web_solver import enumerate_web_outline_candidates
from tests.box_v1.paths import INPUTS


def _web_candidates(member: str, assignment_index: int = 0):
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    assignment = enumerate_view_assignments(build_part_views(source), metadata)[
        assignment_index
    ]
    candidates = enumerate_web_outline_candidates(assignment, metadata).candidates
    return source, assignment, candidates


def test_duplicate_tekla_bolt_objects_collapse_to_eight_physical_holes() -> None:
    source, assignment, candidates = _web_candidates("h-3-cb-2", 1)

    openings = project_circular_openings(source, assignment.h_view)
    web = max(candidates, key=lambda candidate: candidate.area)
    cuts = lower_circular_openings(web.projection, openings)

    assert len(openings) == 8
    assert len(cuts) == 8
    polygon = contour_polygon(web.contour)
    assert all(polygon.covers(Point(cut.center)) for cut in cuts)
    assert {round(cut.radius_mm, 3) for cut in cuts} == {75.0}


def test_bolt_holes_attach_only_to_the_containing_cranked_web_hypothesis() -> None:
    source, assignment, candidates = _web_candidates("h-4-cb-37")

    openings = project_circular_openings(source, assignment.h_view)
    upper = min(candidates, key=lambda candidate: abs(candidate.area - 9_919_225.109))
    lower = min(candidates, key=lambda candidate: abs(candidate.area - 10_241_418.959))

    assert len(openings) == 14
    assert lower_circular_openings(upper.projection, openings) == ()
    cuts = lower_circular_openings(lower.projection, openings)
    assert len(cuts) == 14
    assert {round(cut.radius_mm, 3) for cut in cuts} == {11.0}


def test_view_without_bolt_circles_has_no_manufacturing_openings() -> None:
    source, assignment, _ = _web_candidates("2b1-cb-56")

    assert project_circular_openings(source, assignment.h_view) == ()


def test_hidden_part_semicircle_pairs_reconstruct_one_physical_opening() -> None:
    arcs = tuple(
        SourceEntityIR(
            source_id=f"insert:H/{index}",
            group_id="insert:H",
            handle=str(index),
            kind="ARC",
            layer="Part",
            linetype="XKITLINE04",
            center=(100.0, 50.0),
            radius=20.0,
            start_angle=start,
            end_angle=end,
        )
        for index, (start, end) in enumerate(
            ((0.0, 180.0), (180.0, 0.0), (90.0, 270.0), (270.0, 90.0)),
            start=1,
        )
    )
    dimension = SourceEntityIR(
        source_id="insert:D/1",
        group_id="insert:D",
        handle="1",
        kind="ARC",
        layer="Z-DIMENSIONS",
        linetype="XKITLINE00",
        center=(100.0, 50.0),
        radius=20.0,
        start_angle=0.0,
        end_angle=180.0,
    )
    source = SourceDocumentIR(
        path=Path("synthetic.dxf"),
        dxf_version="AC1021",
        units=4,
        declared_codepage="ANSI_936",
        detected_encoding="utf-8",
        file_sha256="0" * 64,
        geometry_fingerprint="1" * 64,
        groups=(),
        entities=(*arcs, dimension),
    )
    view = PartViewIR(
        group_id="insert:H",
        block_name="H",
        entities=(*arcs, dimension),
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=200.0,
            transverse_min=0.0,
            transverse_max=100.0,
        ),
    )

    openings = project_part_arc_openings(source, view)

    assert len(openings) == 1
    assert openings[0].center == (100.0, 50.0)
    assert openings[0].radius_mm == 20.0
    assert openings[0].visibility is OpeningVisibility.HIDDEN
    assert openings[0].representation_multiplicity == 2
    assert openings[0].source_ids == tuple(entity.source_id for entity in arcs)
