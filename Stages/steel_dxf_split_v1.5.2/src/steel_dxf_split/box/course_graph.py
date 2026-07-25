from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from math import cos, hypot, radians, sin

from .source_ir import SourceEntityIR, is_hidden_projection_linetype
from .view_frame import Point2, ViewFrame


class CourseOrientation(StrEnum):
    LONGITUDINAL = "longitudinal"
    TRANSVERSE = "transverse"
    OBLIQUE = "oblique"
    CURVED = "curved"


@dataclass(frozen=True, slots=True)
class CourseNode:
    node_id: str
    point: Point2
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CourseEdge:
    edge_id: str
    kind: str
    start_node: str
    end_node: str
    start: Point2
    end: Point2
    visible: bool
    orientation: CourseOrientation
    source_ids: tuple[str, ...]
    center: Point2 | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None


@dataclass(frozen=True, slots=True)
class CourseGraph:
    nodes: tuple[CourseNode, ...]
    edges: tuple[CourseEdge, ...]
    endpoint_tolerance_mm: float

    def node(self, node_id: str) -> CourseNode:
        return next(node for node in self.nodes if node.node_id == node_id)

    def edge_by_source_id(self, source_id: str) -> CourseEdge:
        matches = tuple(edge for edge in self.edges if source_id in edge.source_ids)
        if len(matches) != 1:
            raise KeyError(source_id)
        return matches[0]

    @property
    def normalized_signature(self) -> tuple[tuple[object, ...], ...]:
        min_x = min(node.point[0] for node in self.nodes)
        min_y = min(node.point[1] for node in self.nodes)

        def point(value: Point2) -> tuple[float, float]:
            return (round(value[0] - min_x, 6), round(value[1] - min_y, 6))

        return tuple(
            sorted(
                (
                    edge.kind,
                    edge.visible,
                    edge.orientation.value,
                    point(edge.start),
                    point(edge.end),
                    None if edge.center is None else point(edge.center),
                    None if edge.radius is None else round(edge.radius, 6),
                )
                for edge in self.edges
            )
        )


@dataclass(frozen=True, slots=True)
class _PendingEdge:
    source_id: str
    kind: str
    start: Point2
    end: Point2
    visible: bool
    orientation: CourseOrientation
    center: Point2 | None = None
    radius: float | None = None
    start_angle: float | None = None
    end_angle: float | None = None


def _orientation(start: Point2, end: Point2) -> CourseOrientation:
    dx = abs(end[0] - start[0])
    dy = abs(end[1] - start[1])
    length = hypot(dx, dy)
    if length <= 1e-12:
        return CourseOrientation.OBLIQUE
    if dx / length >= 0.965925826:  # within 15 degrees of the member axis
        return CourseOrientation.LONGITUDINAL
    if dy / length >= 0.965925826:
        return CourseOrientation.TRANSVERSE
    return CourseOrientation.OBLIQUE


def _line_pending(entity: SourceEntityIR, frame: ViewFrame) -> _PendingEdge:
    assert entity.start is not None
    assert entity.end is not None
    start = frame.world_to_local(entity.start)
    end = frame.world_to_local(entity.end)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    # Sort on the segment's dominant local coordinate.  A nearly transverse
    # line can carry a 1e-14 longitudinal residue after a rigid rotation; a
    # raw tuple comparison would let that residue reverse semantic direction.
    reverse = end[0] < start[0] if abs(dx) >= abs(dy) else end[1] < start[1]
    if reverse:
        start, end = end, start
    return _PendingEdge(
        source_id=entity.source_id,
        kind="LINE",
        start=start,
        end=end,
        visible=not is_hidden_projection_linetype(entity.linetype),
        orientation=_orientation(start, end),
    )


def _arc_pending(entity: SourceEntityIR, frame: ViewFrame) -> _PendingEdge:
    assert entity.center is not None
    assert entity.radius is not None
    assert entity.start_angle is not None
    assert entity.end_angle is not None
    start_radians = radians(entity.start_angle)
    end_radians = radians(entity.end_angle)
    start_world = (
        entity.center[0] + entity.radius * cos(start_radians),
        entity.center[1] + entity.radius * sin(start_radians),
    )
    end_world = (
        entity.center[0] + entity.radius * cos(end_radians),
        entity.center[1] + entity.radius * sin(end_radians),
    )
    return _PendingEdge(
        source_id=entity.source_id,
        kind="ARC",
        start=frame.world_to_local(start_world),
        end=frame.world_to_local(end_world),
        visible=not is_hidden_projection_linetype(entity.linetype),
        orientation=CourseOrientation.CURVED,
        center=frame.world_to_local(entity.center),
        radius=entity.radius,
        start_angle=entity.start_angle,
        end_angle=entity.end_angle,
    )


def _cluster_endpoints(
    pending: tuple[_PendingEdge, ...],
    tolerance: float,
) -> tuple[tuple[CourseNode, ...], dict[tuple[int, int], str]]:
    endpoints = tuple(
        (point, edge.source_id, edge_index, endpoint_index)
        for edge_index, edge in enumerate(pending)
        for endpoint_index, point in enumerate((edge.start, edge.end))
    )
    parent = list(range(len(endpoints)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[max(first_root, second_root)] = min(first_root, second_root)

    for first in range(len(endpoints)):
        for second in range(first + 1, len(endpoints)):
            first_point = endpoints[first][0]
            second_point = endpoints[second][0]
            if (
                hypot(
                    first_point[0] - second_point[0],
                    first_point[1] - second_point[1],
                )
                <= tolerance
            ):
                union(first, second)

    clusters: dict[int, list[int]] = {}
    for index in range(len(endpoints)):
        clusters.setdefault(find(index), []).append(index)
    ordered_clusters = sorted(
        clusters.values(),
        key=lambda members: (
            sum(endpoints[index][0][0] for index in members) / len(members),
            sum(endpoints[index][0][1] for index in members) / len(members),
        ),
    )
    nodes: list[CourseNode] = []
    endpoint_nodes: dict[tuple[int, int], str] = {}
    for node_index, members in enumerate(ordered_clusters):
        node_id = f"n{node_index:04d}"
        point = (
            sum(endpoints[index][0][0] for index in members) / len(members),
            sum(endpoints[index][0][1] for index in members) / len(members),
        )
        source_ids = tuple(sorted({endpoints[index][1] for index in members}))
        nodes.append(CourseNode(node_id=node_id, point=point, source_ids=source_ids))
        for index in members:
            endpoint_nodes[(endpoints[index][2], endpoints[index][3])] = node_id
    return tuple(nodes), endpoint_nodes


def build_course_graph(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    endpoint_tolerance_mm: float = 0.15,
) -> CourseGraph:
    """Build a source-conserving LINE/ARC graph in one Part local frame."""

    if endpoint_tolerance_mm <= 0:
        raise ValueError("endpoint_tolerance_mm must be positive")
    pending: list[_PendingEdge] = []
    for entity in entities:
        if entity.layer.casefold() != "part":
            continue
        if (
            entity.kind == "LINE"
            and entity.start is not None
            and entity.end is not None
        ):
            pending.append(_line_pending(entity, frame))
        elif entity.kind == "ARC":
            pending.append(_arc_pending(entity, frame))
    if not pending:
        raise ValueError("Course graph requires Part LINE or ARC geometry")
    ordered = tuple(sorted(pending, key=lambda edge: edge.source_id))
    nodes, endpoint_nodes = _cluster_endpoints(ordered, endpoint_tolerance_mm)
    edges = tuple(
        CourseEdge(
            edge_id=f"e{index:04d}",
            kind=edge.kind,
            start_node=endpoint_nodes[(index, 0)],
            end_node=endpoint_nodes[(index, 1)],
            start=edge.start,
            end=edge.end,
            visible=edge.visible,
            orientation=edge.orientation,
            source_ids=(edge.source_id,),
            center=edge.center,
            radius=edge.radius,
            start_angle=edge.start_angle,
            end_angle=edge.end_angle,
        )
        for index, edge in enumerate(ordered)
    )
    return CourseGraph(
        nodes=nodes,
        edges=edges,
        endpoint_tolerance_mm=endpoint_tolerance_mm,
    )
