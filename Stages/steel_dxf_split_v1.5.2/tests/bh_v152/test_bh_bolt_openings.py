from __future__ import annotations

import ezdxf
import pytest
from shapely import affinity
from shapely.geometry import Polygon

from steel_dxf_split.bh_bolt_semantics import opening_nominal_width
from steel_dxf_split.bh_extractor import (
    OwnedPolygonalOpening,
    _collect_bolt_line_openings,
    _compact_bolt_line_side_counts,
    _polygonize_bolt_line_openings,
    _match_polygon_interiors_to_openings,
    collect_bh_instances,
)
from steel_dxf_split.bh_geometry import PartBlock
from steel_dxf_split.geometry_types import BoundingBox


def test_closed_bolt_outline_is_a_physical_opening_but_center_and_open_lines_are_not() -> None:
    document = ezdxf.new("R2000")
    modelspace = document.modelspace()
    outline = [
        (-11.0, -6.5),
        (-7.778, -14.278),
        (0.0, -17.5),
        (7.778, -14.278),
        (11.0, -6.5),
        (11.0, 6.5),
        (7.778, 14.278),
        (0.0, 17.5),
        (-7.778, 14.278),
        (-11.0, 6.5),
    ]
    for start, end in zip(outline, (*outline[1:], outline[0]), strict=True):
        modelspace.add_line(start, end, dxfattribs={"layer": "Bolt"})

    # Tekla also emits center marks.  They cross the opening but do not form
    # manufacturing boundaries by themselves.
    modelspace.add_line((-22.0, 0.0), (22.0, 0.0), dxfattribs={"layer": "Bolt"})
    modelspace.add_line((0.0, -22.0), (0.0, 22.0), dxfattribs={"layer": "Bolt"})
    modelspace.add_line((100.0, 0.0), (120.0, 0.0), dxfattribs={"layer": "Bolt"})

    openings = _polygonize_bolt_line_openings(list(modelspace))

    assert len(openings) == 1
    assert openings[0].bounds == pytest.approx((-11.0, -17.5, 11.0, 17.5))
    assert openings[0].area == pytest.approx(628.232, abs=0.01)


def test_non_bolt_closed_annotation_geometry_is_not_a_physical_opening() -> None:
    document = ezdxf.new("R2000")
    modelspace = document.modelspace()
    for start, end in (
        ((0.0, 0.0), (10.0, 0.0)),
        ((10.0, 0.0), (10.0, 10.0)),
        ((10.0, 10.0), (0.0, 10.0)),
        ((0.0, 10.0), (0.0, 0.0)),
    ):
        modelspace.add_line(start, end, dxfattribs={"layer": "BoltMark"})

    assert _polygonize_bolt_line_openings(list(modelspace)) == []


def test_branched_or_chorded_linework_is_not_promoted_as_multiple_openings() -> None:
    document = ezdxf.new("R2000")
    modelspace = document.modelspace()
    square = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)]
    for start, end in zip(square, (*square[1:], square[0]), strict=True):
        modelspace.add_line(start, end, dxfattribs={"layer": "Bolt"})
    modelspace.add_line((0.0, 0.0), (40.0, 40.0), dxfattribs={"layer": "Bolt"})

    assert _polygonize_bolt_line_openings(list(modelspace)) == []


def test_polygonal_opening_nominal_width_is_rotation_invariant() -> None:
    opening = Polygon(((-11.0, -17.5), (11.0, -17.5), (11.0, 17.5), (-11.0, 17.5)))

    assert opening_nominal_width(opening) == pytest.approx(22.0)
    assert opening_nominal_width(affinity.rotate(opening, 37.0)) == pytest.approx(22.0)


def test_opening_sources_follow_geometry_after_shapely_reorders_interiors() -> None:
    plate = Polygon(
        ((0.0, 0.0), (200.0, 0.0), (200.0, 100.0), (0.0, 100.0)),
        holes=[((80.0, 40.0), (90.0, 40.0), (90.0, 50.0), (80.0, 50.0))],
    )
    narrow = Polygon(((20.0, 20.0), (42.0, 20.0), (42.0, 55.0), (20.0, 55.0)))
    wide = Polygon(((130.0, 20.0), (160.0, 20.0), (160.0, 60.0), (130.0, 60.0)))
    opened = plate.difference(narrow.union(wide))
    openings = [
        OwnedPolygonalOpening(wide, ("wide",), ("B",), None),
        OwnedPolygonalOpening(narrow, ("narrow",), ("A",), None),
    ]

    matches = _match_polygon_interiors_to_openings(opened, openings)

    assert len(matches) == 3
    assert sorted(
        match.source_ids[0] for match in matches if match is not None
    ) == ["narrow", "wide"]
    assert sum(match is None for match in matches) == 1


def test_only_closed_bolt_loops_owned_by_selected_plate_are_collected() -> None:
    document = ezdxf.new("R2000")
    block = document.blocks.new("BOLT_OPENINGS")
    for offset_x in (0.0, 200.0):
        points = [
            (offset_x - 10.0, -15.0),
            (offset_x + 10.0, -15.0),
            (offset_x + 10.0, 15.0),
            (offset_x - 10.0, 15.0),
        ]
        for start, end in zip(points, (*points[1:], points[0]), strict=True):
            block.add_line(start, end, dxfattribs={"layer": "Bolt"})
    insert = document.modelspace().add_blockref("BOLT_OPENINGS", (0.0, 0.0))
    instances = collect_bh_instances(document)
    selected_plate = Polygon(((-50.0, -50.0), (50.0, -50.0), (50.0, 50.0), (-50.0, 50.0)))
    view = PartBlock(
        insert=insert,
        entities=[],
        bbox=BoundingBox(-50.0, -50.0, 50.0, 50.0),
    )

    openings, source_blocks = _collect_bolt_line_openings(
        instances,
        view,
        selected_plate,
    )

    assert len(openings) == 1
    assert openings[0].geometry.bounds == pytest.approx((-10.0, -15.0, 10.0, 15.0))
    assert openings[0].source_ids
    assert source_blocks == ["BOLT_OPENINGS"]


def test_closed_opening_lines_do_not_pollute_flange_edge_symbol_side_counts() -> None:
    document = ezdxf.new("R2000")
    block = document.blocks.new("BOLT_SYMBOLS")
    slot = [(189.0, 135.0), (211.0, 135.0), (211.0, 165.0), (189.0, 165.0)]
    for start, end in zip(slot, (*slot[1:], slot[0]), strict=True):
        block.add_line(start, end, dxfattribs={"layer": "Bolt"})
    block.add_line((178.0, 150.0), (222.0, 150.0), dxfattribs={"layer": "Bolt"})
    block.add_line((200.0, 128.0), (200.0, 172.0), dxfattribs={"layer": "Bolt"})
    block.add_line((500.0, 296.0), (500.0, 324.0), dxfattribs={"layer": "Bolt"})
    for x in (488.0, 512.0):
        block.add_line((x, 299.0), (x, 321.0), dxfattribs={"layer": "Bolt"})
    isolated = document.blocks.new("ISOLATED_HELPER")
    isolated.add_line((489.0, -10.0), (511.0, -10.0), dxfattribs={"layer": "Bolt"})
    insert = document.modelspace().add_blockref("BOLT_SYMBOLS", (0.0, 0.0))
    document.modelspace().add_blockref("ISOLATED_HELPER", (0.0, 0.0))
    instances = collect_bh_instances(document)
    view = PartBlock(
        insert=insert,
        entities=[],
        bbox=BoundingBox(0.0, 0.0, 1000.0, 300.0),
    )
    web = Polygon(((0.0, 0.0), (1000.0, 0.0), (1000.0, 300.0), (0.0, 300.0)))

    counts = _compact_bolt_line_side_counts(
        instances,
        view,
        web,
        long_axis="x",
        nominal_length=1000.0,
    )

    assert counts == {"low": 0, "high": 1}
