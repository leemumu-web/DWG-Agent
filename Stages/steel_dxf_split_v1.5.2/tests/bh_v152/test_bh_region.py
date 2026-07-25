from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from shapely.geometry import Polygon

from steel_dxf_split.bh_models import BulgeContour, BulgeVertex, CircularCut
from steel_dxf_split.bh_region import (
    MAX_ARC_STEP_DEGREES,
    MAX_SAGITTA_MM,
    add_circle_region,
    add_contour_region,
    region_boundary,
)
from steel_dxf_split.geometry_types import Point2D


def _rectangle() -> BulgeContour:
    return BulgeContour(
        [
            BulgeVertex(0.0, 0.0),
            BulgeVertex(100.0, 0.0),
            BulgeVertex(100.0, 50.0),
            BulgeVertex(0.0, 50.0),
        ]
    )


def test_contour_region_round_trips_as_one_planar_face(tmp_path: Path) -> None:
    document = ezdxf.new("R2007")
    entity = add_contour_region(document, _rectangle(), layer="PLATE_CUT")
    output = tmp_path / "rectangle-region.dxf"
    document.saveas(output)

    saved = ezdxf.readfile(output).modelspace().query("REGION")[0]
    boundary = region_boundary(saved)

    assert entity.dxftype() == "REGION"
    assert saved.dxf.layer == "PLATE_CUT"
    assert boundary.face_count == 1
    assert boundary.vertices == (
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 50.0),
        (0.0, 50.0),
    )
    assert boundary.area_mm2 == 5000.0


def test_bulge_region_respects_visual_error_contract() -> None:
    contour = BulgeContour(
        [
            BulgeVertex(0.0, 0.0, 1.0),
            BulgeVertex(20.0, 0.0),
            BulgeVertex(20.0, 20.0),
            BulgeVertex(0.0, 20.0),
        ]
    )
    document = ezdxf.new("R2007")
    entity = add_contour_region(document, contour, layer="PLATE_CUT")
    boundary = region_boundary(entity)

    arc_vertex_count = sum(1 for _, y in boundary.vertices if y < 0.0)
    radius = 10.0
    maximum_step = math.radians(MAX_ARC_STEP_DEGREES)
    expected_step_limit = min(
        maximum_step,
        2.0 * math.acos(1.0 - MAX_SAGITTA_MM / radius),
    )
    actual_step = math.pi / (arc_vertex_count + 1)

    assert arc_vertex_count > 2
    assert actual_step <= expected_step_limit + 1e-12
    assert radius * (1.0 - math.cos(actual_step / 2.0)) <= MAX_SAGITTA_MM
    assert boundary.vertices[0] == (0.0, 0.0)
    assert (20.0, 0.0) in boundary.vertices


def test_circle_region_is_deterministic_and_within_two_microns() -> None:
    cut = CircularCut(Point2D(25.0, 30.0), 11.0)
    first_doc = ezdxf.new("R2007")
    second_doc = ezdxf.new("R2007")

    first = region_boundary(add_circle_region(first_doc, cut, layer="CUT_HOLE"))
    second = region_boundary(add_circle_region(second_doc, cut, layer="CUT_HOLE"))
    radial_errors = [
        abs(math.hypot(x - 25.0, y - 30.0) - 11.0)
        for x, y in first.vertices
    ]

    assert first.vertices == second.vertices
    assert first.face_count == 1
    # SAT v700 serializes coordinates with six significant digits.  Vertex
    # quantization and the chord midpoint deviation must fit together inside
    # the public two-micron visual contract.
    assert max(radial_errors) <= 0.0001
    segment_step = math.tau / len(first.vertices)
    assert (
        max(radial_errors)
        + 11.0 * (1.0 - math.cos(segment_step / 2.0))
        <= MAX_SAGITTA_MM
    )

    first_sat = first_doc.modelspace().query("REGION")[0].sat
    second_sat = second_doc.modelspace().query("REGION")[0].sat
    assert first_sat == second_sat
    assert "Thu Jan 01 00:00:00 1970" in first_sat[1]


def test_sat_round_trip_keeps_large_coordinates_inside_visual_contract(
    tmp_path: Path,
) -> None:
    contour = BulgeContour(
        [
            BulgeVertex(1234.56789, 13200.12345),
            BulgeVertex(5511.22989, 13200.12345),
            BulgeVertex(5511.22989, 13700.12345),
            BulgeVertex(1234.56789, 13700.12345),
        ]
    )
    document = ezdxf.new("R2007")
    add_contour_region(document, contour, layer="PLATE_CUT")
    output = tmp_path / "large-coordinate-region.dxf"
    document.saveas(output)

    saved = ezdxf.readfile(output).modelspace().query("REGION")[0]
    actual = Polygon(region_boundary(saved).vertices)
    expected = Polygon(
        [(vertex.x, vertex.y) for vertex in contour.vertices]
    )

    assert expected.hausdorff_distance(actual) <= MAX_SAGITTA_MM
