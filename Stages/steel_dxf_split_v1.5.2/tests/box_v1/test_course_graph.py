from __future__ import annotations

from math import cos, radians, sin

import pytest

from steel_dxf_split.box.course_graph import (
    CourseOrientation,
    build_course_graph,
)
from steel_dxf_split.box.source_ir import SourceEntityIR
from steel_dxf_split.box.view_frame import derive_view_frame


def _rotate(point: tuple[float, float], angle: float) -> tuple[float, float]:
    theta = radians(angle)
    return (
        point[0] * cos(theta) - point[1] * sin(theta),
        point[0] * sin(theta) + point[1] * cos(theta),
    )


def _line(
    source_id: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    linetype: str = "XKITLINE00",
    angle: float = 0.0,
) -> SourceEntityIR:
    return SourceEntityIR(
        source_id=source_id,
        group_id="insert:test",
        handle=source_id,
        kind="LINE",
        layer="Part",
        linetype=linetype,
        start=_rotate(start, angle),
        end=_rotate(end, angle),
    )


def test_course_graph_keeps_visibility_orientation_and_source_ids() -> None:
    entities = (
        _line("outer-bottom", (0.0, 0.0), (100.0, 0.0)),
        _line("outer-top", (100.0, 30.0), (0.0, 30.0)),
        _line(
            "inner-bottom",
            (0.0, 5.0),
            (100.0, 5.0),
            linetype="XKITLINE04",
        ),
        _line(
            "inner-top",
            (100.0, 25.0),
            (0.0, 25.0),
            linetype="XKITLINE04",
        ),
        _line("left", (0.0, 0.0), (0.0, 30.0)),
        _line("right", (100.0, 0.0), (100.0, 30.0)),
    )
    graph = build_course_graph(entities, derive_view_frame(entities))

    assert len(graph.edges) == 6
    assert len(graph.nodes) == 8
    assert {edge.source_ids for edge in graph.edges} == {
        (entity.source_id,) for entity in entities
    }
    assert sum(edge.visible for edge in graph.edges) == 4
    assert (
        sum(edge.orientation is CourseOrientation.LONGITUDINAL for edge in graph.edges)
        == 4
    )
    assert (
        sum(edge.orientation is CourseOrientation.TRANSVERSE for edge in graph.edges)
        == 2
    )


def test_course_graph_clusters_numerically_equal_endpoints() -> None:
    entities = (
        _line("a", (0.0, 0.0), (50.0, 0.0)),
        _line("b", (50.00004, 0.00003), (100.0, 0.0)),
        _line("extent", (0.0, 10.0), (100.0, 10.0)),
    )
    graph = build_course_graph(
        entities,
        derive_view_frame(entities),
        endpoint_tolerance_mm=0.001,
    )

    a = graph.edge_by_source_id("a")
    b = graph.edge_by_source_id("b")
    assert a.end_node == b.start_node
    assert graph.node(a.end_node).source_ids == ("a", "b")


def test_course_graph_preserves_arc_primitive_and_endpoints() -> None:
    entities = (
        _line("axis-a", (0.0, 0.0), (100.0, 0.0)),
        _line("axis-b", (0.0, 20.0), (100.0, 20.0)),
        SourceEntityIR(
            source_id="round-end",
            group_id="insert:test",
            handle="arc",
            kind="ARC",
            layer="Part",
            linetype="XKITLINE00",
            center=(100.0, 10.0),
            radius=10.0,
            start_angle=-90.0,
            end_angle=90.0,
        ),
    )
    graph = build_course_graph(entities, derive_view_frame(entities))
    arc = graph.edge_by_source_id("round-end")

    assert arc.kind == "ARC"
    assert arc.center == pytest.approx((50.0, 0.0))
    assert arc.radius == pytest.approx(10.0)
    assert graph.node(arc.start_node).point == pytest.approx((50.0, -10.0))
    assert graph.node(arc.end_node).point == pytest.approx((50.0, 10.0))


def test_course_graph_signature_is_rotation_and_order_invariant() -> None:
    base = (
        _line("bottom", (0.0, 0.0), (100.0, 0.0)),
        _line("top", (0.0, 20.0), (100.0, 20.0)),
        _line("left", (0.0, 0.0), (0.0, 20.0)),
        _line("right", (100.0, 0.0), (100.0, 20.0)),
    )
    rotated = tuple(
        reversed(
            tuple(
                _line(
                    entity.source_id,
                    entity.start or (0.0, 0.0),
                    entity.end or (0.0, 0.0),
                    angle=73.0,
                )
                for entity in base
            )
        )
    )

    first = build_course_graph(base, derive_view_frame(base))
    second = build_course_graph(rotated, derive_view_frame(rotated))
    assert first.normalized_signature == second.normalized_signature
