from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Polygon

from steel_dxf_split.box.box_region import (
    MAX_ARC_STEP_DEGREES,
    MAX_SAGITTA_MM,
    add_circle_region,
    add_contour_region,
    region_boundary,
    set_region_boundary,
)
from steel_dxf_split.box.manufacturing_ir import (
    CircularCutIR,
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    rectangle_contour,
)

EVIDENCE = FeatureEvidence(
    EvidenceState.DIRECT,
    ("source:region-test",),
    ("BOX.RULE.REGION.TEST",),
    ("BOX.PROOF.REGION.TEST",),
)


def _segment(
    index: int,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    bulge: float = 0.0,
) -> ContourSegmentIR:
    return ContourSegmentIR(
        segment_id=f"segment:{index}",
        start=start,
        end=end,
        bulge=bulge,
        evidence=EVIDENCE,
    )


def test_contour_region_round_trips_as_one_planar_face(tmp_path: Path) -> None:
    contour = rectangle_contour(0.0, 0.0, 100.0, 50.0, EVIDENCE)
    document = ezdxf.new("R2007")
    entity = add_contour_region(document, contour, layer="PLATE_CUT")
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


def test_set_region_boundary_preserves_entity_identity_and_xdata() -> None:
    document = ezdxf.new("R2007")
    document.appids.add("BOX_DXF_SPLIT")
    entity = add_contour_region(
        document,
        rectangle_contour(0.0, 0.0, 100.0, 50.0, EVIDENCE),
        layer="PLATE_CUT",
    )
    entity.set_xdata("BOX_DXF_SPLIT", [(1000, "binding")])
    handle = entity.dxf.handle

    set_region_boundary(
        entity,
        ((0.0, 0.0), (110.0, 0.0), (110.0, 50.0), (0.0, 50.0)),
    )

    assert entity.dxf.handle == handle
    xdata = tuple((tag.code, tag.value) for tag in entity.get_xdata("BOX_DXF_SPLIT"))
    assert xdata == ((1000, "binding"),)
    assert region_boundary(entity).area_mm2 == 5500.0


def test_bulge_region_respects_visual_error_contract() -> None:
    contour = (
        _segment(0, (0.0, 0.0), (20.0, 0.0), bulge=1.0),
        _segment(1, (20.0, 0.0), (20.0, 20.0)),
        _segment(2, (20.0, 20.0), (0.0, 20.0)),
        _segment(3, (0.0, 20.0), (0.0, 0.0)),
    )
    document = ezdxf.new("R2007")
    entity = add_contour_region(document, contour, layer="PLATE_CUT")
    boundary = region_boundary(entity)

    arc_vertex_count = sum(1 for _, y in boundary.vertices if y < 0.0)
    radius = 10.0
    expected_step_limit = min(
        math.radians(MAX_ARC_STEP_DEGREES),
        2.0 * math.acos(1.0 - MAX_SAGITTA_MM / radius),
    )
    actual_step = math.pi / (arc_vertex_count + 1)

    assert arc_vertex_count > 2
    assert actual_step <= expected_step_limit + 1e-12
    assert radius * (1.0 - math.cos(actual_step / 2.0)) <= MAX_SAGITTA_MM
    assert boundary.vertices[0] == (0.0, 0.0)
    assert (20.0, 0.0) in boundary.vertices


def test_circle_region_is_deterministic_and_within_two_microns() -> None:
    cut = CircularCutIR("cut", (25.0, 30.0), 11.0, EVIDENCE)
    first_document = ezdxf.new("R2007")
    second_document = ezdxf.new("R2007")

    first = region_boundary(add_circle_region(first_document, cut, layer="CUT_HOLE"))
    second = region_boundary(add_circle_region(second_document, cut, layer="CUT_HOLE"))
    radial_errors = [
        abs(math.hypot(x - 25.0, y - 30.0) - 11.0) for x, y in first.vertices
    ]

    assert first.vertices == second.vertices
    assert first.face_count == 1
    assert max(radial_errors) <= 1e-9
    segment_step = math.tau / len(first.vertices)
    assert (
        max(radial_errors) + 11.0 * (1.0 - math.cos(segment_step / 2.0))
        <= MAX_SAGITTA_MM
    )
    first_sat = first_document.modelspace().query("REGION")[0].sat
    second_sat = second_document.modelspace().query("REGION")[0].sat
    assert first_sat == second_sat
    assert "Thu Jan 01 00:00:00 1970" in first_sat[1]


def test_sat_round_trip_keeps_large_coordinates_inside_visual_contract(
    tmp_path: Path,
) -> None:
    contour = rectangle_contour(
        1234.56789,
        13200.12345,
        5511.22989,
        13700.12345,
        EVIDENCE,
    )
    document = ezdxf.new("R2007")
    add_contour_region(document, contour, layer="PLATE_CUT")
    output = tmp_path / "large-coordinate-region.dxf"
    document.saveas(output)

    saved = ezdxf.readfile(output).modelspace().query("REGION")[0]
    actual = Polygon(region_boundary(saved).vertices)
    expected = Polygon([segment.start for segment in contour])

    assert expected.hausdorff_distance(actual) <= MAX_SAGITTA_MM


def test_open_manufacturing_contour_is_rejected() -> None:
    contour = (
        _segment(0, (0.0, 0.0), (10.0, 0.0)),
        _segment(1, (10.0, 0.0), (10.0, 10.0)),
        _segment(2, (10.0, 10.0), (0.0, 9.0)),
    )

    with pytest.raises(ValueError, match="end-to-start closed"):
        add_contour_region(ezdxf.new("R2007"), contour, layer="PLATE_CUT")
