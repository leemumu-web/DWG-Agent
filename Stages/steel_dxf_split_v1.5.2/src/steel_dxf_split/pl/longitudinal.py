from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from math import atan2, cos, degrees, dist, isclose, isfinite, sin
from typing import cast

from ezdxf.entities import Arc, DXFEntity, Ellipse, Line
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from .contracts import (
    LongitudinalIntervalEvidence,
    LongitudinalProof,
    PLSplitError,
    StationBand,
)
from .geometry import (
    FLATTEN_SAGITTA_MM,
    TOPOLOGY_TOLERANCE_MM,
    flatten_entity,
    validate_closed_outline,
)

_SIDE_TOLERANCE_MM = TOPOLOGY_TOLERANCE_MM + FLATTEN_SAGITTA_MM
_TURN_TOLERANCE_MM = TOPOLOGY_TOLERANCE_MM
_FLOAT_EPSILON_MM = 1e-9
_COURSE_CONTINUITY_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class _RingEdge:
    index: int
    entity: DXFEntity
    points: tuple[tuple[float, float], ...]
    start_node: int
    end_node: int
    source_handle: str
    topology_reconstructed: bool


@dataclass(frozen=True, slots=True)
class _SideFragment:
    edge: _RingEdge
    points: tuple[tuple[float, float], ...]
    path_index: int
    traverses_left_to_right: bool

    @property
    def left_x(self) -> float:
        return self.points[0][0]

    @property
    def right_x(self) -> float:
        return self.points[-1][0]


@dataclass(frozen=True, slots=True)
class _SideCourse:
    fragments: tuple[_SideFragment, ...]

    @property
    def left_x(self) -> float:
        return self.fragments[0].left_x

    @property
    def right_x(self) -> float:
        return self.fragments[-1].right_x


@dataclass(frozen=True, slots=True)
class _SideEvent:
    x_mm: float
    source_entity_indices: tuple[int, ...]
    projection_eligible: bool
    correspondence_ids: frozenset[int]


@dataclass(frozen=True, slots=True)
class _RawStationBand:
    upper_x_mm: float
    lower_x_mm: float
    source_entity_indices: tuple[int, ...]


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parents = list(range(size))

    def find(self, value: int) -> int:
        while self.parents[value] != value:
            self.parents[value] = self.parents[self.parents[value]]
            value = self.parents[value]
        return value

    def join(self, first: int, second: int) -> None:
        first_root = self.find(first)
        second_root = self.find(second)
        if first_root != second_root:
            self.parents[second_root] = first_root


def _topology_error(message_zh: str) -> PLSplitError:
    return PLSplitError("LONGITUDINAL_TOPOLOGY", message_zh)


def _source_handle(entity: DXFEntity) -> str:
    return str(entity.dxf.get("handle") or "virtual")


def canonical_boundary_indices(entities: tuple[DXFEntity, ...]) -> tuple[int, ...]:
    linework = tuple(
        LineString(flatten_entity(entity, FLATTEN_SAGITTA_MM)) for entity in entities
    )
    retained = set(range(len(entities)))
    for index in sorted(retained, key=lambda item: (linework[item].length, item)):
        others = tuple(linework[item] for item in retained if item != index)
        if others and unary_union(others).buffer(
            FLATTEN_SAGITTA_MM,
            cap_style="square",
            join_style="mitre",
        ).covers(linework[index]):
            retained.remove(index)
    return tuple(sorted(retained))


def _has_degree_two_endpoints(entities: tuple[DXFEntity, ...]) -> bool:
    endpoints = [
        point
        for entity in entities
        for point in (
            flatten_entity(entity, FLATTEN_SAGITTA_MM)[0],
            flatten_entity(entity, FLATTEN_SAGITTA_MM)[-1],
        )
    ]
    groups = _UnionFind(len(endpoints))
    for index, point in enumerate(endpoints):
        for previous in range(index):
            if dist(point, endpoints[previous]) <= TOPOLOGY_TOLERANCE_MM:
                groups.join(index, previous)
    degrees: dict[int, int] = {}
    for index in range(len(endpoints)):
        root = groups.find(index)
        degrees[root] = degrees.get(root, 0) + 1
    return bool(degrees) and all(degree == 2 for degree in degrees.values())


def canonical_boundary_entities(
    entities: tuple[DXFEntity, ...],
) -> tuple[DXFEntity, ...]:
    selected = tuple(entities[index] for index in canonical_boundary_indices(entities))
    if _has_degree_two_endpoints(selected) or any(
        entity.dxftype() != "LINE" for entity in selected
    ):
        return selected
    polygon = validate_closed_outline(
        entities,
        tolerance_mm=TOPOLOGY_TOLERANCE_MM,
    )
    linework = tuple(
        LineString(flatten_entity(entity, FLATTEN_SAGITTA_MM)) for entity in selected
    )
    boundary_zone = polygon.boundary.buffer(
        _SIDE_TOLERANCE_MM,
        cap_style="square",
        join_style="mitre",
    )
    if any(not boundary_zone.covers(line) for line in linework):
        return selected
    result: list[DXFEntity] = []
    coordinates = tuple(polygon.exterior.coords)
    for start, end in pairwise(coordinates):
        if dist(start, end) <= _FLOAT_EPSILON_MM:
            continue
        segment = LineString((start, end))
        source_index = min(
            range(len(selected)),
            key=lambda index: (linework[index].distance(segment), index),
        )
        clone = cast(Line, selected[source_index].copy())
        clone.dxf.start = (start[0], start[1], 0.0)
        clone.dxf.end = (end[0], end[1], 0.0)
        result.append(clone)
    return tuple(result)


def _ring_edges(
    entities: tuple[DXFEntity, ...],
) -> tuple[tuple[_RingEdge, ...], dict[int, tuple[float, float]]]:
    if not entities:
        raise _topology_error("主视图没有可分析的外边界实体。")
    cloned: list[tuple[int, DXFEntity, tuple[tuple[float, float], ...], str]] = []
    endpoints: list[tuple[int, int, tuple[float, float]]] = []
    boundary_entities = canonical_boundary_entities(entities)
    topology_reconstructed = any(
        all(entity is not source for source in entities) for entity in boundary_entities
    )
    for index, entity in enumerate(boundary_entities):
        clone = entity.copy()
        points = flatten_entity(clone, FLATTEN_SAGITTA_MM)
        cloned.append((index, clone, points, _source_handle(entity)))
        endpoints.append((index, 0, points[0]))
        endpoints.append((index, 1, points[-1]))

    groups = _UnionFind(len(endpoints))
    for index, (_, _, point) in enumerate(endpoints):
        for previous in range(index):
            if dist(point, endpoints[previous][2]) <= TOPOLOGY_TOLERANCE_MM:
                groups.join(index, previous)
    members: dict[int, list[tuple[float, float]]] = {}
    for index, (_, _, point) in enumerate(endpoints):
        members.setdefault(groups.find(index), []).append(point)
    roots = {root: node for node, root in enumerate(sorted(members))}
    coordinates = {
        roots[root]: (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
        for root, points in members.items()
    }
    node_by_endpoint = {
        (edge_index, endpoint): roots[groups.find(index)]
        for index, (edge_index, endpoint, _) in enumerate(endpoints)
    }
    edges = tuple(
        _RingEdge(
            index=index,
            entity=clone,
            points=points,
            start_node=node_by_endpoint[index, 0],
            end_node=node_by_endpoint[index, 1],
            source_handle=handle,
            topology_reconstructed=topology_reconstructed,
        )
        for index, clone, points, handle in cloned
    )
    return edges, coordinates


def _ordered_ring(entities: tuple[DXFEntity, ...]) -> tuple[_RingEdge, ...]:
    edges, coordinates = _ring_edges(entities)
    edge_by_index = {edge.index: edge for edge in edges}
    adjacency: dict[int, list[int]] = {}
    for edge in edges:
        if edge.start_node == edge.end_node:
            raise _topology_error("主视图纵向外边界含自环实体。")
        adjacency.setdefault(edge.start_node, []).append(edge.index)
        adjacency.setdefault(edge.end_node, []).append(edge.index)
    if not adjacency or any(len(indices) != 2 for indices in adjacency.values()):
        raise _topology_error("主视图纵向外边界不是二度闭合环。")

    start_node = min(
        adjacency,
        key=lambda node: (
            coordinates[node][0],
            coordinates[node][1],
            min(adjacency[node]),
        ),
    )
    ordered: list[_RingEdge] = []
    used: set[int] = set()
    current_node = start_node
    previous_edge: int | None = None
    while len(ordered) < len(edges):
        candidates = [
            index
            for index in adjacency[current_node]
            if index != previous_edge and index not in used
        ]
        if not candidates:
            raise _topology_error("主视图纵向外边界含断开或提前闭合的分支。")

        def candidate_key(
            index: int,
            node: int = current_node,
        ) -> tuple[float, float, int]:
            edge = edge_by_index[index]
            other = edge.end_node if edge.start_node == node else edge.start_node
            return coordinates[other][0], coordinates[other][1], index

        edge_index = min(candidates, key=candidate_key)
        edge = edge_by_index[edge_index]
        if edge.start_node == current_node:
            oriented = edge
            next_node = edge.end_node
        else:
            oriented = _RingEdge(
                index=edge.index,
                entity=edge.entity,
                points=tuple(reversed(edge.points)),
                start_node=edge.end_node,
                end_node=edge.start_node,
                source_handle=edge.source_handle,
                topology_reconstructed=edge.topology_reconstructed,
            )
            next_node = edge.start_node
        ordered.append(oriented)
        used.add(edge_index)
        previous_edge = edge_index
        current_node = next_node
    if current_node != start_node or len(used) != len(edges):
        raise _topology_error("主视图纵向外边界不是唯一连通闭合环。")
    return tuple(ordered)


def _section_bounds(geometry: BaseGeometry) -> tuple[tuple[float, float], ...]:
    if isinstance(geometry, LineString):
        coordinates = tuple(geometry.coords)
        if not coordinates:
            return ()
        values = tuple(float(point[1]) for point in coordinates)
        return ((min(values), max(values)),)
    if isinstance(geometry, Point):
        value = float(geometry.y)
        return ((value, value),)
    bounds: list[tuple[float, float]] = []
    if isinstance(geometry, (GeometryCollection, MultiLineString, MultiPoint)):
        for part in geometry.geoms:
            bounds.extend(_section_bounds(part))
    return tuple(bounds)


def _vertical_sections(
    polygon: Polygon, x_mm: float
) -> tuple[tuple[float, float], ...]:
    min_x, min_y, max_x, max_y = polygon.bounds
    if x_mm < min_x - _SIDE_TOLERANCE_MM or x_mm > max_x + _SIDE_TOLERANCE_MM:
        return ()
    padding = max(max_x - min_x, max_y - min_y, 1.0) + 1.0
    vertical = LineString(((x_mm, min_y - padding), (x_mm, max_y + padding)))
    return _section_bounds(polygon.intersection(vertical))


def _polygon_side_y(polygon: Polygon, x_mm: float, side: str) -> float:
    sections = _vertical_sections(polygon, x_mm)
    if not sections:
        raise _topology_error("纵向站位无法投影到主视图材料区域。")
    if side == "upper":
        return max(section[1] for section in sections)
    return min(section[0] for section in sections)


def _point_side(polygon: Polygon, point: Point) -> str | None:
    sections = _vertical_sections(polygon, float(point.x))
    if not sections:
        return None
    lower = min(section[0] for section in sections)
    upper = max(section[1] for section in sections)
    upper_match = abs(float(point.y) - upper) <= _SIDE_TOLERANCE_MM
    lower_match = abs(float(point.y) - lower) <= _SIDE_TOLERANCE_MM
    if upper_match != lower_match:
        return "upper" if upper_match else "lower"
    return None


def _edge_side_fragments(
    edge: _RingEdge,
    polygon: Polygon,
    first_path_index: int,
) -> tuple[tuple[str, _SideFragment], ...]:
    runs: list[tuple[str, int, list[tuple[float, float]]]] = []
    for start, end in pairwise(edge.points):
        if dist(start, end) <= 1e-12:
            continue
        midpoint = Point((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
        side = _point_side(polygon, midpoint)
        delta_x = end[0] - start[0]
        direction = (
            1
            if delta_x > _FLOAT_EPSILON_MM
            else -1
            if delta_x < -_FLOAT_EPSILON_MM
            else 0
        )
        if side is None or direction == 0:
            continue
        if (
            runs
            and runs[-1][0] == side
            and runs[-1][1] == direction
            and dist(runs[-1][2][-1], start) <= 1e-12
        ):
            runs[-1][2].append(end)
            continue
        runs.append((side, direction, [start, end]))

    fragments: list[tuple[str, _SideFragment]] = []
    for offset, (side, _, run) in enumerate(runs):
        traverses_left_to_right = run[0][0] <= run[-1][0]
        points = tuple(run if traverses_left_to_right else reversed(run))
        if points[-1][0] - points[0][0] <= _FLOAT_EPSILON_MM:
            continue
        fragments.append(
            (
                side,
                _SideFragment(
                    edge=edge,
                    points=points,
                    path_index=first_path_index + offset,
                    traverses_left_to_right=traverses_left_to_right,
                ),
            )
        )
    return tuple(fragments)


def _ordered_side_fragments(
    fragments: list[_SideFragment],
) -> tuple[_SideFragment, ...]:
    if len(fragments) < 2:
        return tuple(fragments)
    centers = tuple(
        (fragment.left_x + fragment.right_x) / 2.0 for fragment in fragments
    )
    increasing = all(
        following >= previous - TOPOLOGY_TOLERANCE_MM
        for previous, following in pairwise(centers)
    )
    decreasing = all(
        following <= previous + TOPOLOGY_TOLERANCE_MM
        for previous, following in pairwise(centers)
    )
    if not increasing and not decreasing:
        raise _topology_error("主视图同侧纵向片段没有形成单调连通链。")
    return tuple(fragments if increasing else reversed(fragments))


def _native_course_geometry(
    fragment: _SideFragment,
    *,
    at_left: bool,
) -> tuple[tuple[float, float], float]:
    point = fragment.points[0 if at_left else -1]
    entity = fragment.edge.entity
    entity_type = entity.dxftype()
    if entity_type == "LINE":
        line = cast(Line, entity)
        tangent = (
            float(line.dxf.end.x - line.dxf.start.x),
            float(line.dxf.end.y - line.dxf.start.y),
        )
        curvature = 0.0
    elif entity_type == "ARC":
        arc = cast(Arc, entity)
        construction = arc.construction_tool()
        angle = degrees(
            atan2(
                point[1] - float(construction.center.y),
                point[0] - float(construction.center.x),
            )
        )
        native = next(iter(construction.tangents((angle,))))
        tangent = (float(native.x), float(native.y))
        curvature = 1.0 / float(construction.radius)
    elif entity_type == "ELLIPSE":
        ellipse = cast(Ellipse, entity)
        construction = ellipse.construction_tool()
        param = next(iter(construction.params_from_vertices((point,))))
        native = next(iter(construction.tangents((param,))))
        tangent = (float(native.x), float(native.y))
        major = float(construction.major_axis.magnitude)
        minor = float(construction.minor_axis.magnitude)
        denominator = (
            major * major * sin(param) ** 2 + minor * minor * cos(param) ** 2
        ) ** 1.5
        curvature = major * minor / denominator
    else:
        raise _topology_error(f"主视图纵向链含不支持的实体：{entity_type}")

    length = dist((0.0, 0.0), tangent)
    if length <= _FLOAT_EPSILON_MM:
        raise _topology_error("主视图纵向链端点没有有效切向。")
    tangent = (tangent[0] / length, tangent[1] / length)
    if at_left:
        chord = (
            fragment.points[1][0] - fragment.points[0][0],
            fragment.points[1][1] - fragment.points[0][1],
        )
    else:
        chord = (
            fragment.points[-1][0] - fragment.points[-2][0],
            fragment.points[-1][1] - fragment.points[-2][1],
        )
    if tangent[0] * chord[0] + tangent[1] * chord[1] < 0.0:
        tangent = (-tangent[0], -tangent[1])
        curvature = -curvature
    return tangent, curvature


def _same_geometric_course(
    first: _SideFragment,
    second: _SideFragment,
) -> bool:
    if dist(first.points[-1], second.points[0]) > TOPOLOGY_TOLERANCE_MM:
        return False
    if (
        first.edge.topology_reconstructed
        and second.edge.topology_reconstructed
        and first.edge.entity.dxftype() == second.edge.entity.dxftype() == "LINE"
    ):
        first_chord = (
            first.points[-1][0] - first.points[0][0],
            first.points[-1][1] - first.points[0][1],
        )
        second_chord = (
            second.points[-1][0] - second.points[0][0],
            second.points[-1][1] - second.points[0][1],
        )
        if first_chord[0] * second_chord[0] + first_chord[1] * second_chord[1] > 0.0:
            combined = LineString((first.points[0], second.points[-1]))
            if combined.distance(Point(first.points[-1])) <= TOPOLOGY_TOLERANCE_MM:
                return True
    first_tangent, first_curvature = _native_course_geometry(first, at_left=False)
    second_tangent, second_curvature = _native_course_geometry(second, at_left=True)
    cross = abs(
        first_tangent[0] * second_tangent[1] - first_tangent[1] * second_tangent[0]
    )
    dot = first_tangent[0] * second_tangent[0] + first_tangent[1] * second_tangent[1]
    return (
        dot >= 1.0 - _COURSE_CONTINUITY_EPSILON
        and cross <= _COURSE_CONTINUITY_EPSILON
        and isclose(
            first_curvature,
            second_curvature,
            rel_tol=_COURSE_CONTINUITY_EPSILON,
            abs_tol=_COURSE_CONTINUITY_EPSILON,
        )
    )


def _coalesce_fragments(
    fragments: tuple[_SideFragment, ...],
) -> tuple[_SideCourse, ...]:
    groups: list[list[_SideFragment]] = []
    for fragment in fragments:
        if groups and _same_geometric_course(groups[-1][-1], fragment):
            groups[-1].append(fragment)
        else:
            groups.append([fragment])
    return tuple(_SideCourse(tuple(group)) for group in groups)


def _side_courses(
    ring: tuple[_RingEdge, ...],
    polygon: Polygon,
) -> tuple[
    tuple[_SideCourse, ...],
    tuple[_SideCourse, ...],
    frozenset[tuple[int, str, int]],
]:
    by_side: dict[str, list[_SideFragment]] = {"upper": [], "lower": []}
    traversal: list[tuple[str, _SideFragment]] = []
    for edge in ring:
        edge_fragments = _edge_side_fragments(edge, polygon, len(traversal))
        for side, fragment in edge_fragments:
            by_side[side].append(fragment)
            traversal.append((side, fragment))
    ordered = {
        side: _ordered_side_fragments(fragments) for side, fragments in by_side.items()
    }
    if not ordered["upper"] or not ordered["lower"]:
        raise _topology_error("主视图没有形成可配对的上下纵向边界。")
    end_transition_links: set[tuple[int, str, int]] = set()
    cyclic_traversal = (*traversal, traversal[0])
    for transition_id, ((first_side, first), (second_side, second)) in enumerate(
        pairwise(cyclic_traversal)
    ):
        if first_side == second_side:
            continue
        first_opposite_end = "left" if first.traverses_left_to_right else "right"
        second_opposite_end = "right" if second.traverses_left_to_right else "left"
        end_transition_links.add((first.path_index, first_opposite_end, transition_id))
        end_transition_links.add(
            (second.path_index, second_opposite_end, transition_id)
        )
    return (
        _coalesce_fragments(ordered["upper"]),
        _coalesce_fragments(ordered["lower"]),
        frozenset(end_transition_links),
    )


def _side_events(
    courses: tuple[_SideCourse, ...],
    polygon: Polygon,
    thickness_mm: float,
    end_transition_links: frozenset[tuple[int, str, int]],
) -> tuple[_SideEvent, ...]:
    links_by_boundary: dict[tuple[int, str], set[int]] = {}
    for path_index, boundary, transition_id in end_transition_links:
        links_by_boundary.setdefault((path_index, boundary), set()).add(transition_id)
    values: list[tuple[float, tuple[int, ...], frozenset[int]]] = []
    for course in courses:
        first = course.fragments[0]
        last = course.fragments[-1]
        left_links = frozenset(
            transition_id
            for fragment in course.fragments
            for transition_id in links_by_boundary.get(
                (fragment.path_index, "left"), ()
            )
        )
        right_links = frozenset(
            transition_id
            for fragment in course.fragments
            for transition_id in links_by_boundary.get(
                (fragment.path_index, "right"), ()
            )
        )
        values.append(
            (
                course.left_x,
                (first.edge.index,),
                left_links,
            )
        )
        values.append(
            (
                course.right_x,
                (last.edge.index,),
                right_links,
            )
        )
        for previous, following in pairwise(course.fragments):
            x_mm = (previous.right_x + following.left_x) / 2.0
            near_end = min(x_mm - polygon.bounds[0], polygon.bounds[2] - x_mm)
            if (
                not previous.edge.topology_reconstructed
                and not following.edge.topology_reconstructed
                and near_end <= thickness_mm + 0.1
            ):
                values.append(
                    (
                        x_mm,
                        (previous.edge.index, following.edge.index),
                        frozenset(),
                    )
                )
    values.sort(key=lambda item: (item[0], item[1]))
    groups: list[list[tuple[float, tuple[int, ...], frozenset[int]]]] = []
    for value in values:
        if not groups or value[0] - groups[-1][-1][0] > TOPOLOGY_TOLERANCE_MM:
            groups.append([value])
        else:
            groups[-1].append(value)
    min_x, _, max_x, _ = polygon.bounds
    events: list[_SideEvent] = []
    for index, group in enumerate(groups):
        x_mm = sum(value[0] for value in group) / len(group)
        sources = tuple(
            sorted(
                {source for _, source_indices, _ in group for source in source_indices}
            )
        )
        correspondence_ids = frozenset(
            transition_id for value in group for transition_id in value[2]
        )
        crosses_reentrant_end = any(
            len(_vertical_sections(polygon, x_mm + offset)) > 1
            for offset in (-FLATTEN_SAGITTA_MM, 0.0, FLATTEN_SAGITTA_MM)
        )
        if crosses_reentrant_end:
            correspondence_ids = frozenset()
        near_end = min(x_mm - min_x, max_x - x_mm) <= thickness_mm + 0.1
        events.append(
            _SideEvent(
                x_mm=x_mm,
                source_entity_indices=sources,
                projection_eligible=(
                    index not in {0, len(groups) - 1}
                    and (near_end or crosses_reentrant_end or bool(correspondence_ids))
                ),
                correspondence_ids=correspondence_ids,
            )
        )
    return tuple(events)


def _required_event_partners(
    upper: tuple[_SideEvent, ...],
    lower: tuple[_SideEvent, ...],
) -> tuple[tuple[int | None, ...], tuple[int | None, ...]]:
    def internal_links(events: tuple[_SideEvent, ...]) -> dict[int, int]:
        result: dict[int, int] = {}
        for index, event in enumerate(events[1:-1], start=1):
            for transition_id in event.correspondence_ids:
                if transition_id in result and result[transition_id] != index:
                    raise _topology_error("同一端链转折对应到多个纵向站位。")
                result[transition_id] = index
        return result

    upper_links = internal_links(upper)
    lower_links = internal_links(lower)
    upper_partners: list[int | None] = [None] * len(upper)
    lower_partners: list[int | None] = [None] * len(lower)
    for transition_id in sorted(upper_links.keys() & lower_links.keys()):
        upper_index = upper_links[transition_id]
        lower_index = lower_links[transition_id]
        if upper_partners[upper_index] not in {None, lower_index}:
            raise _topology_error("上边界站位对应到多个下边界站位。")
        if lower_partners[lower_index] not in {None, upper_index}:
            raise _topology_error("下边界站位对应到多个上边界站位。")
        upper_partners[upper_index] = lower_index
        lower_partners[lower_index] = upper_index
    return tuple(upper_partners), tuple(lower_partners)


def _station_width_within_limit(width_mm: float, limit_mm: float) -> bool:
    return width_mm <= limit_mm or isclose(
        width_mm,
        limit_mm,
        rel_tol=0.0,
        abs_tol=_FLOAT_EPSILON_MM,
    )


def _project_single_end_offset(
    upper: tuple[_SideEvent, ...],
    lower: tuple[_SideEvent, ...],
    min_x: float,
    max_x: float,
    station_limit_mm: float,
) -> tuple[tuple[_SideEvent, ...], tuple[_SideEvent, ...]]:
    if len(upper) != 3 or len(lower) != 3:
        return upper, lower
    upper_event = upper[1]
    lower_event = lower[1]
    if not upper_event.correspondence_ids & lower_event.correspondence_ids:
        return upper, lower
    if _station_width_within_limit(
        abs(upper_event.x_mm - lower_event.x_mm),
        station_limit_mm,
    ):
        return upper, lower
    midpoint = (min_x + max_x) / 2.0
    upper_at_left = upper_event.x_mm <= midpoint
    lower_at_left = lower_event.x_mm <= midpoint
    if upper_at_left != lower_at_left:
        return upper, lower
    end_distance = (
        min(upper_event.x_mm, lower_event.x_mm) - min_x
        if upper_at_left
        else max_x - max(upper_event.x_mm, lower_event.x_mm)
    )
    if end_distance > station_limit_mm:
        return upper, lower

    def independent(event: _SideEvent) -> _SideEvent:
        return _SideEvent(
            x_mm=event.x_mm,
            source_entity_indices=event.source_entity_indices,
            projection_eligible=True,
            correspondence_ids=frozenset(),
        )

    return (
        (upper[0], independent(upper_event), upper[2]),
        (lower[0], independent(lower_event), lower[2]),
    )


def _opposite_short_end_bands(
    upper: tuple[_SideEvent, ...],
    lower: tuple[_SideEvent, ...],
    min_x: float,
    max_x: float,
    station_limit_mm: float,
) -> tuple[_RawStationBand, ...] | None:
    if len(upper) != 3 or len(lower) != 3:
        return None
    upper_event = upper[1]
    lower_event = lower[1]
    if not upper_event.correspondence_ids & lower_event.correspondence_ids:
        return None
    midpoint = (min_x + max_x) / 2.0
    upper_at_left = upper_event.x_mm <= midpoint
    lower_at_left = lower_event.x_mm <= midpoint
    if upper_at_left == lower_at_left:
        return None
    if upper_at_left:
        leading = upper_event
        upper_body_span = upper[-1].x_mm - upper_event.x_mm
        lower_body_span = lower_event.x_mm - lower[0].x_mm
        trailing = _RawStationBand(
            upper_x_mm=upper[-1].x_mm,
            lower_x_mm=lower_event.x_mm,
            source_entity_indices=tuple(
                sorted(
                    {
                        *upper[-1].source_entity_indices,
                        *lower_event.source_entity_indices,
                    }
                )
            ),
        )
        leading_distance = upper_event.x_mm - min_x
        trailing_distance = max_x - lower_event.x_mm
    else:
        leading = lower_event
        upper_body_span = upper_event.x_mm - upper[0].x_mm
        lower_body_span = lower[-1].x_mm - lower_event.x_mm
        trailing = _RawStationBand(
            upper_x_mm=upper_event.x_mm,
            lower_x_mm=lower[-1].x_mm,
            source_entity_indices=tuple(
                sorted(
                    {
                        *upper_event.source_entity_indices,
                        *lower[-1].source_entity_indices,
                    }
                )
            ),
        )
        leading_distance = lower_event.x_mm - min_x
        trailing_distance = max_x - upper_event.x_mm
    if not all(
        _station_width_within_limit(distance_mm, station_limit_mm)
        for distance_mm in (leading_distance, trailing_distance)
    ):
        return None
    if abs(upper_body_span - lower_body_span) > TOPOLOGY_TOLERANCE_MM:
        return None
    return (
        _RawStationBand(
            upper_x_mm=upper[0].x_mm,
            lower_x_mm=lower[0].x_mm,
            source_entity_indices=tuple(
                sorted(
                    {
                        *upper[0].source_entity_indices,
                        *lower[0].source_entity_indices,
                    }
                )
            ),
        ),
        _RawStationBand(
            upper_x_mm=leading.x_mm,
            lower_x_mm=leading.x_mm,
            source_entity_indices=leading.source_entity_indices,
        ),
        trailing,
    )


def _align_events(
    upper: tuple[_SideEvent, ...],
    lower: tuple[_SideEvent, ...],
    station_limit_mm: float,
) -> tuple[_RawStationBand, ...]:
    upper_partners, lower_partners = _required_event_partners(upper, lower)

    @cache
    def solve(
        first: int,
        second: int,
    ) -> tuple[float, tuple[str, ...]] | None:
        if first == len(upper) and second == len(lower):
            return 0.0, ()
        candidates: list[tuple[float, int, tuple[str, ...]]] = []
        if first < len(upper) and second < len(lower):
            required_match = (
                upper_partners[first] == second and lower_partners[second] == first
            )
            match_width = abs(upper[first].x_mm - lower[second].x_mm)
            independently_projectable = (
                upper[first].projection_eligible and lower[second].projection_eligible
            )
            unconstrained_match = (
                upper_partners[first] is None
                and lower_partners[second] is None
                and (
                    not independently_projectable
                    or _station_width_within_limit(match_width, station_limit_mm)
                )
            )
            if required_match or unconstrained_match:
                tail = solve(first + 1, second + 1)
                if tail is not None:
                    cost, operations = tail
                    candidates.append(
                        (
                            match_width + cost,
                            0,
                            ("match", *operations),
                        )
                    )
        if (
            first < len(upper)
            and upper[first].projection_eligible
            and upper_partners[first] is None
        ):
            tail = solve(first + 1, second)
            if tail is not None:
                cost, operations = tail
                candidates.append((station_limit_mm + cost, 1, ("upper", *operations)))
        if (
            second < len(lower)
            and lower[second].projection_eligible
            and lower_partners[second] is None
        ):
            tail = solve(first, second + 1)
            if tail is not None:
                cost, operations = tail
                candidates.append((station_limit_mm + cost, 2, ("lower", *operations)))
        if not candidates:
            return None
        cost, _, operations = min(
            candidates,
            key=lambda item: (item[0], item[1]),
        )
        return cost, operations

    solution = solve(0, 0)
    if solution is None:
        raise _topology_error("上下纵向链的内部站位没有唯一拓扑对应。")
    _, operations = solution
    upper_index = 0
    lower_index = 0
    bands: list[_RawStationBand] = []
    for operation in operations:
        if operation == "match":
            upper_event = upper[upper_index]
            lower_event = lower[lower_index]
            bands.append(
                _RawStationBand(
                    upper_x_mm=upper_event.x_mm,
                    lower_x_mm=lower_event.x_mm,
                    source_entity_indices=tuple(
                        sorted(
                            {
                                *upper_event.source_entity_indices,
                                *lower_event.source_entity_indices,
                            }
                        )
                    ),
                )
            )
            upper_index += 1
            lower_index += 1
        elif operation == "upper":
            event = upper[upper_index]
            bands.append(
                _RawStationBand(
                    upper_x_mm=event.x_mm,
                    lower_x_mm=event.x_mm,
                    source_entity_indices=event.source_entity_indices,
                )
            )
            upper_index += 1
        else:
            event = lower[lower_index]
            bands.append(
                _RawStationBand(
                    upper_x_mm=event.x_mm,
                    lower_x_mm=event.x_mm,
                    source_entity_indices=event.source_entity_indices,
                )
            )
            lower_index += 1
    return tuple(bands)


def _edge_indices_at_x(
    ring: tuple[_RingEdge, ...],
    x_mm: float,
) -> tuple[int, ...]:
    return tuple(
        edge.index
        for edge in ring
        if min(point[0] for point in edge.points) - TOPOLOGY_TOLERANCE_MM
        <= x_mm
        <= max(point[0] for point in edge.points) + TOPOLOGY_TOLERANCE_MM
    )


def _merge_bands(
    bands: tuple[_RawStationBand, ...],
) -> tuple[_RawStationBand, ...]:
    ordered = sorted(
        bands,
        key=lambda band: (
            (band.upper_x_mm + band.lower_x_mm) / 2.0,
            band.upper_x_mm,
            band.lower_x_mm,
        ),
    )
    merged: list[_RawStationBand] = []
    for band in ordered:
        if (
            merged
            and abs(band.upper_x_mm - merged[-1].upper_x_mm) <= TOPOLOGY_TOLERANCE_MM
            and abs(band.lower_x_mm - merged[-1].lower_x_mm) <= TOPOLOGY_TOLERANCE_MM
        ):
            previous = merged[-1]
            merged[-1] = _RawStationBand(
                upper_x_mm=(previous.upper_x_mm + band.upper_x_mm) / 2.0,
                lower_x_mm=(previous.lower_x_mm + band.lower_x_mm) / 2.0,
                source_entity_indices=tuple(
                    sorted(
                        {
                            *previous.source_entity_indices,
                            *band.source_entity_indices,
                        }
                    )
                ),
            )
        else:
            merged.append(band)
    return tuple(merged)


def _station_bands(
    ring: tuple[_RingEdge, ...],
    polygon: Polygon,
    upper: tuple[_SideCourse, ...],
    lower: tuple[_SideCourse, ...],
    end_transition_links: frozenset[tuple[int, str, int]],
    thickness_mm: float,
) -> tuple[StationBand, ...]:
    limit = thickness_mm + 0.1
    upper_events = _side_events(upper, polygon, thickness_mm, end_transition_links)
    lower_events = _side_events(lower, polygon, thickness_mm, end_transition_links)
    min_x, _, max_x, _ = polygon.bounds
    opposite_end_bands = _opposite_short_end_bands(
        upper_events,
        lower_events,
        min_x,
        max_x,
        limit,
    )
    if opposite_end_bands is None:
        upper_events, lower_events = _project_single_end_offset(
            upper_events,
            lower_events,
            min_x,
            max_x,
            limit,
        )
        opposite_end_bands = _align_events(upper_events, lower_events, limit)
    raw = list(_merge_bands(opposite_end_bands))
    if (
        raw
        and min_x < min(raw[0].upper_x_mm, raw[0].lower_x_mm) - TOPOLOGY_TOLERANCE_MM
    ):
        raw.insert(
            0,
            _RawStationBand(min_x, min_x, _edge_indices_at_x(ring, min_x)),
        )
    if (
        raw
        and max_x > max(raw[-1].upper_x_mm, raw[-1].lower_x_mm) + TOPOLOGY_TOLERANCE_MM
    ):
        raw.append(_RawStationBand(max_x, max_x, _edge_indices_at_x(ring, max_x)))
    if len(raw) < 2:
        raise _topology_error("主视图没有形成至少一个纵向区间。")

    result: list[StationBand] = []
    for index, band in enumerate(raw):
        width = abs(band.upper_x_mm - band.lower_x_mm)
        if not _station_width_within_limit(width, limit):
            raise PLSplitError(
                "STATION_BAND_TOO_WIDE",
                "纵向站带的上下边界错位超过板厚允许值。",
            )
        sources = tuple(
            sorted(
                {
                    *band.source_entity_indices,
                    *_edge_indices_at_x(ring, band.upper_x_mm),
                    *_edge_indices_at_x(ring, band.lower_x_mm),
                }
            )
        )
        result.append(
            StationBand(
                index=index,
                upper_x_mm=band.upper_x_mm,
                lower_x_mm=band.lower_x_mm,
                source_entity_indices=sources,
            )
        )
    for previous, following in pairwise(result):
        if (
            following.upper_x_mm < previous.upper_x_mm - TOPOLOGY_TOLERANCE_MM
            or following.lower_x_mm < previous.lower_x_mm - TOPOLOGY_TOLERANCE_MM
        ):
            raise _topology_error("纵向站带在上下边界上的顺序不一致。")
    return tuple(result)


def _piece_indices_between(
    courses: tuple[_SideCourse, ...],
    first_x: float,
    second_x: float,
) -> tuple[int, ...]:
    left = min(first_x, second_x)
    right = max(first_x, second_x)
    return tuple(
        sorted(
            {
                fragment.edge.index
                for course in courses
                for fragment in course.fragments
                if min(fragment.right_x, right) - max(fragment.left_x, left) > 1e-9
            }
        )
    )


def _boundary_sources(
    ring: tuple[_RingEdge, ...],
    polygon: Polygon,
    x_mm: float,
    side: str,
) -> tuple[int, ...]:
    point = Point(x_mm, _polygon_side_y(polygon, x_mm, side))
    return tuple(
        edge.index
        for edge in ring
        if LineString(edge.points).distance(point) <= _SIDE_TOLERANCE_MM
    )


def _intervals(
    ring: tuple[_RingEdge, ...],
    polygon: Polygon,
    upper: tuple[_SideCourse, ...],
    lower: tuple[_SideCourse, ...],
    stations: tuple[StationBand, ...],
    thickness_mm: float,
) -> tuple[LongitudinalIntervalEvidence, ...]:
    handles = {edge.index: edge.source_handle for edge in ring}
    intervals: list[LongitudinalIntervalEvidence] = []
    for index, (left, right) in enumerate(pairwise(stations)):
        upper_span = right.upper_x_mm - left.upper_x_mm
        lower_span = right.lower_x_mm - left.lower_x_mm
        if upper_span < -TOPOLOGY_TOLERANCE_MM or lower_span < -TOPOLOGY_TOLERANCE_MM:
            raise _topology_error("纵向区间在上下边界上的方向不一致。")
        upper_mid_x = (left.upper_x_mm + right.upper_x_mm) / 2.0
        lower_mid_x = (left.lower_x_mm + right.lower_x_mm) / 2.0
        upper_indices = tuple(
            sorted(
                {
                    *_piece_indices_between(
                        upper,
                        left.upper_x_mm,
                        right.upper_x_mm,
                    ),
                    *_boundary_sources(ring, polygon, upper_mid_x, "upper"),
                }
            )
        )
        lower_indices = tuple(
            sorted(
                {
                    *_piece_indices_between(
                        lower,
                        left.lower_x_mm,
                        right.lower_x_mm,
                    ),
                    *_boundary_sources(ring, polygon, lower_mid_x, "lower"),
                }
            )
        )
        upper_left_y = _polygon_side_y(polygon, left.upper_x_mm, "upper")
        upper_right_y = _polygon_side_y(polygon, right.upper_x_mm, "upper")
        lower_left_y = _polygon_side_y(polygon, left.lower_x_mm, "lower")
        lower_right_y = _polygon_side_y(polygon, right.lower_x_mm, "lower")
        upper_delta_y = upper_right_y - upper_left_y
        lower_delta_y = lower_right_y - lower_left_y

        overlap_left = max(left.upper_x_mm, left.lower_x_mm)
        overlap_right = min(right.upper_x_mm, right.lower_x_mm)
        opposed = overlap_right - overlap_left > 1e-9
        if opposed:
            overlap_x = (overlap_left + overlap_right) / 2.0
            upper_y = _polygon_side_y(polygon, overlap_x, "upper")
            lower_y = _polygon_side_y(polygon, overlap_x, "lower")
            opposed = upper_y - lower_y > TOPOLOGY_TOLERANCE_MM and polygon.buffer(
                FLATTEN_SAGITTA_MM
            ).covers(LineString(((overlap_x, lower_y), (overlap_x, upper_y))))
        left_gap = dist(
            (left.upper_x_mm, upper_left_y),
            (left.lower_x_mm, lower_left_y),
        )
        right_gap = dist(
            (right.upper_x_mm, upper_right_y),
            (right.lower_x_mm, lower_right_y),
        )
        terminal_short = (
            index in {0, len(stations) - 2}
            and min(
                upper_span,
                lower_span,
            )
            <= thickness_mm + 0.1
        )
        is_end_feature = (
            not opposed
            or upper_span <= 1e-9
            or lower_span <= 1e-9
            or terminal_short
            or left_gap <= TOPOLOGY_TOLERANCE_MM
            or right_gap <= TOPOLOGY_TOLERANCE_MM
            or bool(set(upper_indices) & set(lower_indices))
        )
        source_indices = tuple(dict.fromkeys((*upper_indices, *lower_indices)))
        intervals.append(
            LongitudinalIntervalEvidence(
                index=index,
                left_station=left,
                right_station=right,
                upper_entity_indices=upper_indices,
                lower_entity_indices=lower_indices,
                upper_span_mm=max(upper_span, 0.0),
                lower_span_mm=max(lower_span, 0.0),
                upper_delta_y_mm=upper_delta_y,
                lower_delta_y_mm=lower_delta_y,
                is_end_feature=is_end_feature,
                is_turn_candidate=(
                    abs(upper_delta_y) > _TURN_TOLERANCE_MM
                    and abs(lower_delta_y) > _TURN_TOLERANCE_MM
                ),
                source_handles=tuple(handles[source] for source in source_indices),
            )
        )
    return tuple(intervals)


def _consecutive_groups(indices: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    groups: list[list[int]] = []
    for index in indices:
        if not groups or index != groups[-1][-1] + 1:
            groups.append([index])
        else:
            groups[-1].append(index)
    return tuple(tuple(group) for group in groups)


def select_carrier_zone(
    intervals: tuple[LongitudinalIntervalEvidence, ...],
) -> tuple[tuple[int, ...], str]:
    body = tuple(interval for interval in intervals if not interval.is_end_feature)
    if not body:
        raise PLSplitError("CARRIER_MISSING", "主视图没有可用纵向主体区间。")
    turns = tuple(interval.index for interval in body if interval.is_turn_candidate)
    if turns:
        groups = _consecutive_groups(turns)
        by_index = {interval.index: interval for interval in body}
        ranked_groups = sorted(
            groups,
            key=lambda group: (
                -sum(
                    min(by_index[index].upper_span_mm, by_index[index].lower_span_mm)
                    for index in group
                ),
                group,
            ),
        )
        if len(ranked_groups) > 1:
            extents = tuple(
                sum(
                    min(by_index[index].upper_span_mm, by_index[index].lower_span_mm)
                    for index in group
                )
                for group in ranked_groups[:2]
            )
            if extents[0] - extents[1] <= 0.1:
                raise PLSplitError(
                    "CARRIER_AMBIGUOUS",
                    "主视图存在多个不相邻转折承载候选。",
                )
        return ranked_groups[0], "paired_visible_turn"
    ranked = sorted(
        body,
        key=lambda interval: (
            -min(interval.upper_span_mm, interval.lower_span_mm),
            interval.index,
        ),
    )
    if len(ranked) > 1:
        first = min(ranked[0].upper_span_mm, ranked[0].lower_span_mm)
        second = min(ranked[1].upper_span_mm, ranked[1].lower_span_mm)
        if first - second <= 0.1:
            raise PLSplitError(
                "CARRIER_AMBIGUOUS",
                "主视图最长主体区间不唯一。",
            )
    return (ranked[0].index,), "unique_longest_body"


def analyze_longitudinal_outline(
    entities: tuple[DXFEntity, ...],
    polygon: Polygon,
    *,
    thickness_mm: float,
) -> LongitudinalProof:
    if not isfinite(thickness_mm) or thickness_mm <= 0.0:
        raise _topology_error("主视图纵向分析需要正的有限板厚。")
    if polygon.is_empty or not polygon.is_valid or len(polygon.interiors):
        raise _topology_error("主视图材料区域无效或含内轮廓。")
    ring = _ordered_ring(entities)
    boundary_zone = polygon.boundary.buffer(
        TOPOLOGY_TOLERANCE_MM,
        cap_style="flat",
        join_style="mitre",
    )
    if any(not boundary_zone.covers(LineString(edge.points)) for edge in ring):
        raise _topology_error("主视图纵向外边界与材料区域不一致。")
    upper, lower, end_transition_links = _side_courses(ring, polygon)
    stations = _station_bands(
        ring,
        polygon,
        upper,
        lower,
        end_transition_links,
        thickness_mm,
    )
    intervals = _intervals(ring, polygon, upper, lower, stations, thickness_mm)
    carrier, reason = select_carrier_zone(intervals)
    return LongitudinalProof(
        intervals=intervals,
        carrier_interval_indices=carrier,
        selection_reason=reason,
    )
