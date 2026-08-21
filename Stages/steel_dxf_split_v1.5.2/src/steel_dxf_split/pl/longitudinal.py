from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from math import dist, isclose, isfinite

from ezdxf.entities import DXFEntity
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from .contracts import (
    LongitudinalIntervalEvidence,
    LongitudinalProof,
    PLSplitError,
    StationBand,
)
from .geometry import FLATTEN_SAGITTA_MM, TOPOLOGY_TOLERANCE_MM, flatten_entity

_SIDE_TOLERANCE_MM = TOPOLOGY_TOLERANCE_MM + FLATTEN_SAGITTA_MM
_TURN_TOLERANCE_MM = 0.001


@dataclass(frozen=True, slots=True)
class _RingEdge:
    index: int
    entity: DXFEntity
    points: tuple[tuple[float, float], ...]
    start_node: int
    end_node: int
    source_handle: str


@dataclass(frozen=True, slots=True)
class _SidePiece:
    edge: _RingEdge
    points: tuple[tuple[float, float], ...]

    @property
    def left_x(self) -> float:
        return self.points[0][0]

    @property
    def right_x(self) -> float:
        return self.points[-1][0]


@dataclass(frozen=True, slots=True)
class _SideEvent:
    x_mm: float
    source_entity_indices: tuple[int, ...]


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


def _ring_edges(
    entities: tuple[DXFEntity, ...],
) -> tuple[tuple[_RingEdge, ...], dict[int, tuple[float, float]]]:
    if not entities:
        raise _topology_error("主视图没有可分析的外边界实体。")
    cloned: list[tuple[DXFEntity, tuple[tuple[float, float], ...], str]] = []
    endpoints: list[tuple[int, int, tuple[float, float]]] = []
    for index, entity in enumerate(entities):
        clone = entity.copy()
        points = flatten_entity(clone, FLATTEN_SAGITTA_MM)
        cloned.append((clone, points, _source_handle(entity)))
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
        )
        for index, (clone, points, handle) in enumerate(cloned)
    )
    return edges, coordinates


def _ordered_ring(entities: tuple[DXFEntity, ...]) -> tuple[_RingEdge, ...]:
    edges, coordinates = _ring_edges(entities)
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
            edge = edges[index]
            other = edge.end_node if edge.start_node == node else edge.start_node
            return coordinates[other][0], coordinates[other][1], index

        edge_index = min(candidates, key=candidate_key)
        edge = edges[edge_index]
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


def _vertical_sections(polygon: Polygon, x_mm: float) -> tuple[tuple[float, float], ...]:
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
    for lower, upper in sections:
        if not lower - _SIDE_TOLERANCE_MM <= point.y <= upper + _SIDE_TOLERANCE_MM:
            continue
        upper_match = abs(float(point.y) - upper) <= _SIDE_TOLERANCE_MM
        lower_match = abs(float(point.y) - lower) <= _SIDE_TOLERANCE_MM
        if upper_match != lower_match:
            return "upper" if upper_match else "lower"
    return None


def _edge_side(edge: _RingEdge, polygon: Polygon) -> str | None:
    line = LineString(edge.points)
    if line.bounds[2] - line.bounds[0] <= 1e-9:
        return None
    votes = {
        side
        for fraction in (0.25, 0.5, 0.75)
        if (side := _point_side(polygon, line.interpolate(fraction, normalized=True))) is not None
    }
    if len(votes) != 1:
        return None
    return votes.pop()


def _side_pieces(
    ring: tuple[_RingEdge, ...],
    polygon: Polygon,
) -> tuple[tuple[_SidePiece, ...], tuple[_SidePiece, ...]]:
    by_side: dict[str, list[_SidePiece]] = {"upper": [], "lower": []}
    for edge in ring:
        side = _edge_side(edge, polygon)
        if side is None:
            continue
        if edge.points[0][0] <= edge.points[-1][0]:
            points = edge.points
        else:
            points = tuple(reversed(edge.points))
        if points[-1][0] - points[0][0] <= 1e-9:
            continue
        if any(
            following[0] < previous[0] - TOPOLOGY_TOLERANCE_MM
            for previous, following in zip(points, points[1:], strict=False)
        ):
            continue
        by_side[side].append(
            _SidePiece(
                edge=edge,
                points=points,
            )
        )
    for pieces in by_side.values():
        pieces.sort(key=lambda piece: (piece.left_x, piece.right_x, piece.edge.index))
    if not by_side["upper"] or not by_side["lower"]:
        raise _topology_error("主视图没有形成可配对的上下纵向边界。")
    return tuple(by_side["upper"]), tuple(by_side["lower"])


def _side_events(pieces: tuple[_SidePiece, ...]) -> tuple[_SideEvent, ...]:
    values = sorted(
        ((x_mm, piece.edge.index) for piece in pieces for x_mm in (piece.left_x, piece.right_x)),
        key=lambda item: (item[0], item[1]),
    )
    groups: list[list[tuple[float, int]]] = []
    for value in values:
        if not groups or value[0] - groups[-1][-1][0] > TOPOLOGY_TOLERANCE_MM:
            groups.append([value])
        else:
            groups[-1].append(value)
    return tuple(
        _SideEvent(
            x_mm=sum(value[0] for value in group) / len(group),
            source_entity_indices=tuple(sorted({value[1] for value in group})),
        )
        for group in groups
    )


def _align_events(
    upper: tuple[_SideEvent, ...],
    lower: tuple[_SideEvent, ...],
    thickness_mm: float,
) -> tuple[_RawStationBand, ...]:
    skip_cost = thickness_mm + 0.1

    @cache
    def solve(first: int, second: int) -> tuple[float, tuple[str, ...]]:
        if first == len(upper) and second == len(lower):
            return 0.0, ()
        candidates: list[tuple[float, int, tuple[str, ...]]] = []
        if first < len(upper) and second < len(lower):
            cost, operations = solve(first + 1, second + 1)
            candidates.append(
                (
                    abs(upper[first].x_mm - lower[second].x_mm) + cost,
                    0,
                    ("match", *operations),
                )
            )
        if first < len(upper):
            cost, operations = solve(first + 1, second)
            candidates.append((skip_cost + cost, 1, ("upper", *operations)))
        if second < len(lower):
            cost, operations = solve(first, second + 1)
            candidates.append((skip_cost + cost, 2, ("lower", *operations)))
        cost, _, operations = min(candidates, key=lambda item: (item[0], item[1]))
        return cost, operations

    _, operations = solve(0, 0)
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
    upper: tuple[_SidePiece, ...],
    lower: tuple[_SidePiece, ...],
    thickness_mm: float,
) -> tuple[StationBand, ...]:
    raw = list(
        _merge_bands(
            _align_events(
                _side_events(upper),
                _side_events(lower),
                thickness_mm,
            )
        )
    )
    min_x, _, max_x, _ = polygon.bounds
    if raw and min_x < min(raw[0].upper_x_mm, raw[0].lower_x_mm) - TOPOLOGY_TOLERANCE_MM:
        raw.insert(
            0,
            _RawStationBand(min_x, min_x, _edge_indices_at_x(ring, min_x)),
        )
    if raw and max_x > max(raw[-1].upper_x_mm, raw[-1].lower_x_mm) + TOPOLOGY_TOLERANCE_MM:
        raw.append(_RawStationBand(max_x, max_x, _edge_indices_at_x(ring, max_x)))
    if len(raw) < 2:
        raise _topology_error("主视图没有形成至少一个纵向区间。")

    limit = thickness_mm + 0.1
    result: list[StationBand] = []
    for index, band in enumerate(raw):
        width = abs(band.upper_x_mm - band.lower_x_mm)
        if width > limit and not isclose(width, limit, abs_tol=1e-9):
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
    for previous, following in zip(result, result[1:], strict=False):
        if (
            following.upper_x_mm < previous.upper_x_mm - TOPOLOGY_TOLERANCE_MM
            or following.lower_x_mm < previous.lower_x_mm - TOPOLOGY_TOLERANCE_MM
        ):
            raise _topology_error("纵向站带在上下边界上的顺序不一致。")
    return tuple(result)


def _piece_indices_between(
    pieces: tuple[_SidePiece, ...],
    first_x: float,
    second_x: float,
) -> tuple[int, ...]:
    left = min(first_x, second_x)
    right = max(first_x, second_x)
    return tuple(
        piece.edge.index
        for piece in pieces
        if min(piece.right_x, right) - max(piece.left_x, left) > 1e-9
    )


def _boundary_sources(
    ring: tuple[_RingEdge, ...],
    polygon: Polygon,
    x_mm: float,
    side: str,
) -> tuple[int, ...]:
    point = Point(x_mm, _polygon_side_y(polygon, x_mm, side))
    return tuple(
        edge.index for edge in ring if LineString(edge.points).distance(point) <= _SIDE_TOLERANCE_MM
    )


def _intervals(
    ring: tuple[_RingEdge, ...],
    polygon: Polygon,
    upper: tuple[_SidePiece, ...],
    lower: tuple[_SidePiece, ...],
    stations: tuple[StationBand, ...],
) -> tuple[LongitudinalIntervalEvidence, ...]:
    handles = {edge.index: edge.source_handle for edge in ring}
    intervals: list[LongitudinalIntervalEvidence] = []
    for index, (left, right) in enumerate(zip(stations, stations[1:], strict=False)):
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
        is_end_feature = (
            not opposed
            or upper_span <= 1e-9
            or lower_span <= 1e-9
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
        if len(groups) != 1:
            raise PLSplitError(
                "CARRIER_AMBIGUOUS",
                "主视图存在多个不相邻转折承载候选。",
            )
        return groups[0], "paired_visible_turn"
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
    upper, lower = _side_pieces(ring, polygon)
    stations = _station_bands(ring, polygon, upper, lower, thickness_mm)
    intervals = _intervals(ring, polygon, upper, lower, stations)
    carrier, reason = select_carrier_zone(intervals)
    return LongitudinalProof(
        intervals=intervals,
        carrier_interval_indices=carrier,
        selection_reason=reason,
    )
