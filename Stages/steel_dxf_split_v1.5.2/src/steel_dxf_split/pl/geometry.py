from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, dist, hypot, radians, sqrt, tau
from typing import cast

from ezdxf import bbox
from ezdxf.entities import Arc, Circle, DXFEntity, Ellipse, Line
from shapely.affinity import translate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import polygonize_full, unary_union

from .contracts import (
    PlateOutline,
    PLMetadata,
    PLSourceContext,
    PLSplitError,
    SectionProof,
)

TOPOLOGY_TOLERANCE_MM = 0.1
FLATTEN_SAGITTA_MM = 0.001
BOUNDARY_TOLERANCE_MM = 0.05
NOMINAL_WIDTH_TOLERANCE_MM = 1.0
NUMERIC_EPSILON_MM = 1e-6
MAIN_BOUNDARY_AREA_TOLERANCE_MM2 = 0.02
MAX_CONTAINED_CUTOUT_AREA_RATIO = 0.1
FLAT_CURVE_MAX_SAGITTA_MM = 0.7
FLAT_CURVE_ANGLE_TOLERANCE_RAD = radians(0.01)
TIP_SHORT_EDGE_MAX_RATIO = 0.1
TIP_SHORT_CHAIN_MAX_RATIO = 0.15
TIP_AREA_CHANGE_MAX_RATIO = 0.01
TIP_PARALLEL_CROSS_RATIO = 0.0001


@dataclass(frozen=True, slots=True)
class _Segment:
    entity: DXFEntity
    points: tuple[tuple[float, float], ...]
    start_node: int
    end_node: int

    @property
    def line(self) -> LineString:
        return LineString(self.points)


@dataclass(frozen=True, slots=True)
class _Component:
    segments: tuple[_Segment, ...]
    polygon: Polygon


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


def expand_native_segments(entities: tuple[DXFEntity, ...]) -> tuple[DXFEntity, ...]:
    expanded: list[DXFEntity] = []
    for entity in entities:
        if entity.dxf.layer.casefold() != "part":
            continue
        entity_type = entity.dxftype()
        if entity_type in {"LINE", "ARC", "ELLIPSE"}:
            expanded.append(entity)
            continue
        if entity_type in {"LWPOLYLINE", "POLYLINE"}:
            virtual = tuple(entity.virtual_entities())
            if any(item.dxftype() not in {"LINE", "ARC"} for item in virtual):
                raise PLSplitError(
                    "UNSUPPORTED_PART_ENTITY",
                    f"Part 多段线含不支持的原生实体：{entity_type}",
                )
            expanded.extend(virtual)
            continue
        raise PLSplitError(
            "UNSUPPORTED_PART_ENTITY",
            f"Part 图层含不支持的制造实体：{entity_type}",
        )
    if not expanded:
        raise PLSplitError("PART_GEOMETRY_MISSING", "Part 图层没有制造线弧。")
    return tuple(expanded)


def flatten_entity(
    entity: DXFEntity,
    sagitta_mm: float = FLATTEN_SAGITTA_MM,
) -> tuple[tuple[float, float], ...]:
    if sagitta_mm <= 0.0:
        raise ValueError("sagitta_mm must be positive")
    if entity.dxftype() == "LINE":
        line = cast(Line, entity)
        points = (line.dxf.start, line.dxf.end)
    elif entity.dxftype() in {"ARC", "ELLIPSE"}:
        curve = cast(Ellipse, entity)
        points = tuple(curve.flattening(sagitta_mm))
    else:
        raise PLSplitError(
            "UNSUPPORTED_PART_ENTITY",
            f"无法离散制造实体：{entity.dxftype()}",
        )
    result = tuple((float(point.x), float(point.y)) for point in points)
    if len(result) < 2 or all(dist(result[0], point) <= 1e-12 for point in result[1:]):
        raise PLSplitError("ZERO_LENGTH_ENTITY", "Part 图层存在零长度制造实体。")
    return result


def native_entity_length(entity: DXFEntity) -> float:
    if entity.dxftype() == "LINE":
        points = flatten_entity(entity)
        return dist(points[0], points[-1])
    if entity.dxftype() == "ARC":
        span = (float(entity.dxf.end_angle) - float(entity.dxf.start_angle)) % 360.0
        return float(entity.dxf.radius) * radians(span)
    if entity.dxftype() == "ELLIPSE":
        return LineString(flatten_entity(entity, 0.0001)).length
    raise PLSplitError(
        "UNSUPPORTED_PART_ENTITY",
        f"无法计算制造实体长度：{entity.dxftype()}",
    )


def _canonical_nodes(
    points: list[tuple[int, int, tuple[float, float], bool]],
    tolerance_mm: float,
) -> tuple[dict[tuple[int, int], int], dict[int, tuple[float, float]]]:
    groups = _UnionFind(len(points))
    for index, (_, _, point, _) in enumerate(points):
        for previous in range(index):
            if dist(point, points[previous][2]) <= tolerance_mm:
                groups.join(index, previous)
    members: dict[int, list[tuple[float, float, bool]]] = {}
    for index, (_, _, point, curved) in enumerate(points):
        members.setdefault(groups.find(index), []).append((point[0], point[1], curved))
    roots = {root: node for node, root in enumerate(sorted(members))}
    coordinates: dict[int, tuple[float, float]] = {}
    for root, values in members.items():
        curved = [(x, y) for x, y, is_curved in values if is_curved]
        candidates = curved or [(x, y) for x, y, _ in values]
        if curved and any(dist(curved[0], point) > 0.001 for point in curved[1:]):
            raise PLSplitError(
                "CURVE_ENDPOINT_GAP",
                "同一拓扑节点的圆弧或椭圆端点误差超过 0.001 mm。",
            )
        coordinates[roots[root]] = (
            sum(point[0] for point in candidates) / len(candidates),
            sum(point[1] for point in candidates) / len(candidates),
        )
    mapping = {
        (segment, endpoint): roots[groups.find(index)]
        for index, (segment, endpoint, _, _) in enumerate(points)
    }
    return mapping, coordinates


def _segments(
    entities: tuple[DXFEntity, ...],
    tolerance_mm: float,
) -> tuple[_Segment, ...]:
    flattened = [flatten_entity(entity) for entity in entities]
    endpoints: list[tuple[int, int, tuple[float, float], bool]] = []
    for index, (entity, points) in enumerate(zip(entities, flattened, strict=True)):
        curved = entity.dxftype() in {"ARC", "ELLIPSE"}
        endpoints.append((index, 0, points[0], curved))
        endpoints.append((index, 1, points[-1], curved))
    mapping, coordinates = _canonical_nodes(endpoints, tolerance_mm)
    result: list[_Segment] = []
    for index, (entity, points) in enumerate(zip(entities, flattened, strict=True)):
        start_node = mapping[index, 0]
        end_node = mapping[index, 1]
        adjusted = (coordinates[start_node], *points[1:-1], coordinates[end_node])
        result.append(
            _Segment(
                entity=entity,
                points=adjusted,
                start_node=start_node,
                end_node=end_node,
            )
        )
    return tuple(result)


def _connected_components(
    segments: tuple[_Segment, ...],
) -> tuple[tuple[_Segment, ...], ...]:
    groups = _UnionFind(len(segments))
    for index, segment in enumerate(segments):
        nodes = {segment.start_node, segment.end_node}
        for previous in range(index):
            other = segments[previous]
            if nodes & {other.start_node, other.end_node}:
                groups.join(index, previous)
    components: dict[int, list[_Segment]] = {}
    for index, segment in enumerate(segments):
        components.setdefault(groups.find(index), []).append(segment)
    return tuple(tuple(component) for _, component in sorted(components.items()))


def _component_polygon(segments: tuple[_Segment, ...]) -> Polygon | None:
    linework = unary_union(tuple(segment.line for segment in segments))
    polygons, _, _, invalid = polygonize_full(linework)
    if len(invalid.geoms):
        return None
    material = unary_union(tuple(polygons.geoms))
    if isinstance(material, Polygon):
        return material
    if isinstance(material, MultiPolygon) and len(material.geoms) == 1:
        return material.geoms[0]
    return None


def _proved_components(
    entities: tuple[DXFEntity, ...],
    tolerance_mm: float = TOPOLOGY_TOLERANCE_MM,
) -> tuple[_Component, ...]:
    components: list[_Component] = []
    for segments in _connected_components(_segments(entities, tolerance_mm)):
        polygon = _component_polygon(segments)
        if polygon is None or polygon.is_empty or polygon.area <= 1e-6:
            continue
        components.append(_Component(segments=segments, polygon=polygon))
    return tuple(components)


def _entity_handle(entity: DXFEntity) -> str:
    return str(entity.dxf.get("handle") or "virtual")


def _outer_entities(component: _Component) -> tuple[DXFEntity, ...]:
    boundary_zone = component.polygon.boundary.buffer(
        BOUNDARY_TOLERANCE_MM,
        cap_style="flat",
        join_style="mitre",
    )
    selected: list[DXFEntity] = []
    seen: set[str] = set()
    for segment in component.segments:
        if segment.start_node == segment.end_node or segment.line.length <= 1e-9:
            continue
        if segment.entity.dxftype() == "LINE":
            boundary_part = (
                segment.line
                if boundary_zone.covers(segment.line)
                else segment.line.intersection(component.polygon.boundary)
            )
            line_parts = (
                (boundary_part,)
                if isinstance(boundary_part, LineString)
                else tuple(
                    geometry
                    for geometry in getattr(boundary_part, "geoms", ())
                    if isinstance(geometry, LineString)
                )
            )
            for line_part in line_parts:
                if line_part.length <= 1e-9:
                    continue
                fingerprint = line_part.normalize().wkb_hex
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                clone = segment.entity.copy()
                clone.dxf.start = line_part.coords[0]
                clone.dxf.end = line_part.coords[-1]
                selected.append(clone)
            continue
        if boundary_zone.covers(segment.line):
            fingerprint = segment.line.normalize().wkb_hex
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            selected.append(segment.entity.copy())
    if not selected:
        raise PLSplitError("MAIN_BOUNDARY_MISSING", "主视图外边界没有可追溯原生实体。")
    return tuple(selected)


def _circle_arc_group(circle: Circle) -> tuple[DXFEntity, ...]:
    attributes = {
        "center": circle.dxf.center,
        "radius": float(circle.dxf.radius),
        "layer": "Part",
        "extrusion": circle.dxf.extrusion,
    }
    return (
        Arc.new(dxfattribs={**attributes, "start_angle": 0.0, "end_angle": 180.0}),
        Arc.new(dxfattribs={**attributes, "start_angle": 180.0, "end_angle": 360.0}),
    )


def _circle_signature(
    group: tuple[DXFEntity, ...],
) -> tuple[tuple[float, float], float] | None:
    arcs = tuple(cast(Arc, entity) for entity in group if entity.dxftype() == "ARC")
    if not arcs or len(arcs) != len(group):
        return None
    center = (float(arcs[0].dxf.center.x), float(arcs[0].dxf.center.y))
    radius = float(arcs[0].dxf.radius)
    if any(
        dist(center, (float(arc.dxf.center.x), float(arc.dxf.center.y)))
        > TOPOLOGY_TOLERANCE_MM
        or abs(float(arc.dxf.radius) - radius) > TOPOLOGY_TOLERANCE_MM
        for arc in arcs[1:]
    ):
        return None
    return center, radius


def _without_large_circle_covered_centers(
    groups: tuple[tuple[DXFEntity, ...], ...],
) -> tuple[tuple[DXFEntity, ...], ...]:
    signatures = tuple(_circle_signature(group) for group in groups)
    return tuple(
        group
        for index, (group, signature) in enumerate(zip(groups, signatures, strict=True))
        if signature is None
        or not any(
            other_index != index
            and other is not None
            and other[1] - signature[1] >= TOPOLOGY_TOLERANCE_MM
            and dist(signature[0], other[0]) <= other[1] - TOPOLOGY_TOLERANCE_MM
            for other_index, other in enumerate(signatures)
        )
    )


def _native_endpoints(
    entity: DXFEntity,
) -> tuple[tuple[float, float], tuple[float, float]]:
    points = flatten_entity(entity)
    return points[0], points[-1]


def _shared_endpoint_indices(
    first: DXFEntity,
    second: DXFEntity,
) -> tuple[tuple[int, int], ...]:
    first_points = _native_endpoints(first)
    second_points = _native_endpoints(second)
    return tuple(
        (first_index, second_index)
        for first_index, first_point in enumerate(first_points)
        for second_index, second_point in enumerate(second_points)
        if dist(first_point, second_point) <= TOPOLOGY_TOLERANCE_MM
    )


def _flat_circle_compatible(
    entity: DXFEntity,
    center: tuple[float, float],
    radius: float,
) -> bool:
    if entity.dxftype() == "ARC":
        arc = cast(Arc, entity)
        return (
            dist(center, (float(arc.dxf.center.x), float(arc.dxf.center.y)))
            <= TOPOLOGY_TOLERANCE_MM
            and abs(float(arc.dxf.radius) - radius) <= TOPOLOGY_TOLERANCE_MM
        )
    if entity.dxftype() != "LINE":
        return False
    first, second = _native_endpoints(entity)
    if any(
        abs(hypot(point[0] - center[0], point[1] - center[1]) - radius)
        > 0.001
        for point in (first, second)
    ):
        return False
    chord = dist(first, second)
    if chord <= 1e-9 or chord >= 2.0 * radius:
        return False
    sagitta = radius - sqrt(max(0.0, radius * radius - chord * chord / 4.0))
    return sagitta <= FLAT_CURVE_MAX_SAGITTA_MM


def _merge_flat_curve_connector(
    first: DXFEntity,
    second: DXFEntity,
) -> Line | None:
    if first.dxftype() != "LINE" or second.dxftype() != "LINE":
        return None
    first_points = _native_endpoints(first)
    second_points = _native_endpoints(second)
    matches = tuple(
        (first_index, second_index)
        for first_index, first_point in enumerate(first_points)
        for second_index, second_point in enumerate(second_points)
        if dist(first_point, second_point) <= 0.001
    )
    if len(matches) != 1:
        return None
    first_index, second_index = matches[0]
    shared = first_points[first_index]
    first_outer = first_points[1 - first_index]
    second_outer = second_points[1 - second_index]
    first_vector = (shared[0] - first_outer[0], shared[1] - first_outer[1])
    second_vector = (second_outer[0] - shared[0], second_outer[1] - shared[1])
    if first_vector[0] * second_vector[0] + first_vector[1] * second_vector[1] <= 0.0:
        return None
    chord = (second_outer[0] - first_outer[0], second_outer[1] - first_outer[1])
    chord_length = hypot(*chord)
    if chord_length <= 1e-9:
        return None
    deviation = abs(
        (shared[0] - first_outer[0]) * chord[1]
        - (shared[1] - first_outer[1]) * chord[0]
    ) / chord_length
    if deviation > TOPOLOGY_TOLERANCE_MM:
        return None
    merged = cast(Line, first.copy())
    merged.dxf.start = first_outer
    merged.dxf.end = second_outer
    return merged


def _oriented_chain(
    entities: tuple[DXFEntity, ...],
    indices: tuple[int, ...],
    adjacency: dict[int, set[int]],
) -> tuple[tuple[int, tuple[float, float], tuple[float, float]], ...] | None:
    endpoints = tuple(index for index in indices if len(adjacency[index]) == 1)
    if len(endpoints) != 2 or any(len(adjacency[index]) not in {1, 2} for index in indices):
        return None
    order: list[int] = []
    previous: int | None = None
    current = endpoints[0]
    while True:
        order.append(current)
        following = tuple(adjacency[current] - ({previous} if previous is not None else set()))
        if not following:
            break
        if len(following) != 1:
            return None
        previous, current = current, following[0]
    if len(order) != len(indices):
        return None
    oriented: list[tuple[int, tuple[float, float], tuple[float, float]]] = []
    for position, index in enumerate(order):
        points = _native_endpoints(entities[index])
        previous_index = order[position - 1] if position else None
        next_index = order[position + 1] if position + 1 < len(order) else None
        if previous_index is None:
            matches = _shared_endpoint_indices(entities[index], entities[next_index])
            if len(matches) != 1:
                return None
            end_index = matches[0][0]
            start_index = 1 - end_index
        elif next_index is None:
            matches = _shared_endpoint_indices(entities[index], entities[previous_index])
            if len(matches) != 1:
                return None
            start_index = matches[0][0]
            end_index = 1 - start_index
        else:
            previous_matches = _shared_endpoint_indices(
                entities[index], entities[previous_index]
            )
            next_matches = _shared_endpoint_indices(entities[index], entities[next_index])
            if (
                len(previous_matches) != 1
                or len(next_matches) != 1
                or previous_matches[0][0] == next_matches[0][0]
            ):
                return None
            start_index = previous_matches[0][0]
            end_index = next_matches[0][0]
        oriented.append((index, points[start_index], points[end_index]))
    return tuple(oriented)


def _point_angle(point: tuple[float, float], center: tuple[float, float]) -> float:
    return atan2(point[1] - center[1], point[0] - center[0])


def _signed_curve_sweep(
    entity: DXFEntity,
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
) -> float | None:
    if entity.dxftype() == "LINE":
        delta = (_point_angle(end, center) - _point_angle(start, center) + tau / 2.0) % tau
        delta -= tau / 2.0
        return None if abs(delta) <= 1e-9 or abs(abs(delta) - tau / 2.0) <= 1e-9 else delta
    arc = cast(Arc, entity)
    native_start, native_end = _native_endpoints(arc)
    span = radians((float(arc.dxf.end_angle) - float(arc.dxf.start_angle)) % 360.0)
    if span <= 1e-9:
        return None
    if (
        dist(start, native_start) <= TOPOLOGY_TOLERANCE_MM
        and dist(end, native_end) <= TOPOLOGY_TOLERANCE_MM
    ):
        return span
    if (
        dist(start, native_end) <= TOPOLOGY_TOLERANCE_MM
        and dist(end, native_start) <= TOPOLOGY_TOLERANCE_MM
    ):
        return -span
    return None


def _merged_flat_arc(
    entities: tuple[DXFEntity, ...],
    oriented: tuple[tuple[int, tuple[float, float], tuple[float, float]], ...],
    seed_index: int,
) -> Arc | None:
    seed = cast(Arc, entities[seed_index])
    center = (float(seed.dxf.center.x), float(seed.dxf.center.y))
    radius = float(seed.dxf.radius)
    first_point = oriented[0][1]
    last_point = oriented[-1][2]
    endpoint_errors = tuple(
        abs(hypot(point[0] - center[0], point[1] - center[1]) - radius)
        for point in (first_point, last_point)
    )
    if any(error > 0.001 for error in endpoint_errors):
        return None
    sweeps = tuple(
        _signed_curve_sweep(entities[index], start, end, center)
        for index, start, end in oriented
    )
    if any(sweep is None for sweep in sweeps):
        return None
    resolved = cast(tuple[float, ...], sweeps)
    if any(sweep * resolved[0] <= 0.0 for sweep in resolved[1:]):
        return None
    total = sum(resolved)
    if not FLAT_CURVE_ANGLE_TOLERANCE_RAD < abs(total) < tau - FLAT_CURVE_ANGLE_TOLERANCE_RAD:
        return None
    first_angle = _point_angle(first_point, center)
    last_angle = _point_angle(last_point, center)
    endpoint_span = (
        (last_angle - first_angle) % tau
        if total > 0.0
        else (first_angle - last_angle) % tau
    )
    if abs(abs(total) - endpoint_span) > FLAT_CURVE_ANGLE_TOLERANCE_RAD:
        return None
    merged = cast(Arc, seed.copy())
    if total > 0.0:
        merged.dxf.start_angle = degrees(first_angle) % 360.0
        merged.dxf.end_angle = degrees(last_angle) % 360.0
    else:
        merged.dxf.start_angle = degrees(last_angle) % 360.0
        merged.dxf.end_angle = degrees(first_angle) % 360.0
    return merged


def _recover_flat_curve_chains(
    entities: tuple[DXFEntity, ...],
) -> tuple[DXFEntity, ...]:
    consumed: set[int] = set()
    replacements: dict[int, DXFEntity] = {}
    for seed_index, seed in enumerate(entities):
        if seed_index in consumed or seed.dxftype() != "ARC":
            continue
        arc = cast(Arc, seed)
        center = (float(arc.dxf.center.x), float(arc.dxf.center.y))
        radius = float(arc.dxf.radius)
        compatible = {
            index
            for index, entity in enumerate(entities)
            if index not in consumed
            and _flat_circle_compatible(entity, center, radius)
        }
        selected = {seed_index}
        while True:
            additions = {
                index
                for index in compatible - selected
                if any(
                    _shared_endpoint_indices(entities[index], entities[member])
                    for member in selected
                )
            }
            if not additions:
                break
            selected.update(additions)
        if len(selected) < 2 or not any(
            entities[index].dxftype() == "LINE" for index in selected
        ):
            continue
        adjacency = {
            index: {
                other
                for other in selected
                if other != index
                and _shared_endpoint_indices(entities[index], entities[other])
            }
            for index in selected
        }
        indices = tuple(sorted(selected))
        oriented = _oriented_chain(entities, indices, adjacency)
        if oriented is None:
            continue
        connector_merges: list[tuple[int, int, Line]] = []
        trimmed = set(selected)
        for chain_index, external_point in (
            (oriented[0][0], oriented[0][1]),
            (oriented[-1][0], oriented[-1][2]),
        ):
            if entities[chain_index].dxftype() != "LINE":
                continue
            outside = tuple(
                index
                for index, entity in enumerate(entities)
                if index not in selected
                and index not in consumed
                and entity.dxftype() == "LINE"
                and any(
                    dist(external_point, point) <= 0.001
                    for point in _native_endpoints(entity)
                )
            )
            if len(outside) != 1 or any(outside[0] in pair[:2] for pair in connector_merges):
                continue
            connector = _merge_flat_curve_connector(
                entities[chain_index], entities[outside[0]]
            )
            if connector is None:
                continue
            trimmed.remove(chain_index)
            connector_merges.append((chain_index, outside[0], connector))
        if not any(entities[index].dxftype() == "ARC" for index in trimmed):
            continue
        if len(trimmed) == 1:
            merged = entities[next(iter(trimmed))]
            if merged.dxftype() != "ARC":
                continue
            arc_indices: set[int] = set()
        else:
            trimmed_adjacency = {
                index: adjacency[index] & trimmed for index in trimmed
            }
            trimmed_oriented = _oriented_chain(
                entities,
                tuple(sorted(trimmed)),
                trimmed_adjacency,
            )
            if trimmed_oriented is None:
                continue
            trimmed_seed = next(
                index for index in trimmed if entities[index].dxftype() == "ARC"
            )
            merged = _merged_flat_arc(entities, trimmed_oriented, trimmed_seed)
            if merged is None:
                continue
            arc_indices = trimmed
        for chain_index, outside_index, connector in connector_merges:
            connector_indices = {chain_index, outside_index}
            replacements[min(connector_indices)] = connector
            consumed.update(connector_indices)
        if not arc_indices:
            continue
        if merged is None:
            continue
        replacement_index = min(arc_indices)
        replacements[replacement_index] = merged
        consumed.update(arc_indices)
    if not replacements:
        return entities
    result = tuple(
        replacements.get(index, entity)
        for index, entity in enumerate(entities)
        if index not in consumed or index in replacements
    )
    original_bounds = bbox.extents(entities, fast=False)
    result_bounds = bbox.extents(result, fast=False)
    if not original_bounds.has_data or not result_bounds.has_data:
        return entities
    if any(
        abs(first - second) > TOPOLOGY_TOLERANCE_MM
        for first, second in zip(
            (
                original_bounds.extmin.x,
                original_bounds.extmin.y,
                original_bounds.extmax.x,
                original_bounds.extmax.y,
            ),
            (
                result_bounds.extmin.x,
                result_bounds.extmin.y,
                result_bounds.extmax.x,
                result_bounds.extmax.y,
            ),
            strict=True,
        )
    ):
        return entities
    try:
        validate_closed_outline(result)
    except PLSplitError:
        return entities
    return result


def validate_closed_outline(
    entities: tuple[DXFEntity, ...],
    *,
    tolerance_mm: float = 0.001,
) -> Polygon:
    components = _proved_components(entities, tolerance_mm)
    if len(components) != 1:
        raise PLSplitError("OUTLINE_NOT_CLOSED", "制造外轮廓没有形成唯一闭合区域。")
    polygon = components[0].polygon
    if not polygon.is_valid or len(polygon.interiors):
        raise PLSplitError("OUTLINE_INVALID", "制造外轮廓无效或含未定义内轮廓。")
    return polygon


def _without_collinear_boundary_vertices(
    polygon: Polygon,
) -> tuple[tuple[float, float], ...]:
    vertices = [
        (float(x), float(y))
        for x, y in polygon.exterior.coords[:-1]
    ]
    changed = True
    while changed and len(vertices) > 3:
        changed = False
        for index, current in enumerate(vertices):
            previous = vertices[index - 1]
            following = vertices[(index + 1) % len(vertices)]
            span = dist(previous, following)
            if span <= 1e-12:
                continue
            deviation = (
                abs(
                    (following[0] - previous[0]) * (previous[1] - current[1])
                    - (following[1] - previous[1]) * (previous[0] - current[0])
                )
                / span
            )
            projection = (
                (current[0] - previous[0]) * (following[0] - previous[0])
                + (current[1] - previous[1]) * (following[1] - previous[1])
            ) / (span * span)
            if (
                deviation <= BOUNDARY_TOLERANCE_MM
                and -1e-12 <= projection <= 1.0 + 1e-12
            ):
                vertices.pop(index)
                changed = True
                break
    return tuple(vertices)


def _support_line_intersection(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> tuple[float, float] | None:
    first = (
        first_end[0] - first_start[0],
        first_end[1] - first_start[1],
    )
    second = (
        second_end[0] - second_start[0],
        second_end[1] - second_start[1],
    )
    denominator = first[0] * second[1] - first[1] * second[0]
    scale = dist(first_start, first_end) * dist(second_start, second_end)
    if scale <= 1e-12 or abs(denominator) <= scale * 1e-9:
        return None
    offset = (
        second_start[0] - first_start[0],
        second_start[1] - first_start[1],
    )
    factor = (offset[0] * second[1] - offset[1] * second[0]) / denominator
    return (
        first_start[0] + factor * first[0],
        first_start[1] + factor * first[1],
    )


def _point_to_support_line(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    length = dist(start, end)
    if length <= 1e-12:
        return float("inf")
    return (
        abs(
            (end[0] - start[0]) * (start[1] - point[1])
            - (end[1] - start[1]) * (start[0] - point[0])
        )
        / length
    )


def _parallel_same_direction(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    first = (
        first_end[0] - first_start[0],
        first_end[1] - first_start[1],
    )
    second = (
        second_end[0] - second_start[0],
        second_end[1] - second_start[1],
    )
    scale = hypot(*first) * hypot(*second)
    return (
        scale > 1e-12
        and abs(first[0] * second[1] - first[1] * second[0])
        <= scale * TIP_PARALLEL_CROSS_RATIO
        and first[0] * second[0] + first[1] * second[1] > 0.0
    )


def _append_unique_side(
    sides: list[
        tuple[tuple[float, float], tuple[float, float]]
    ],
    side: tuple[tuple[float, float], tuple[float, float]],
) -> None:
    if any(
        dist(side[0], existing[0]) <= 0.001
        and dist(side[1], existing[1]) <= 0.001
        for existing in sides
    ):
        return
    sides.append(side)


def _complete_flat_tip_transition(
    entities: tuple[DXFEntity, ...],
    source_segments: tuple[_Segment, ...],
) -> tuple[DXFEntity, ...]:
    if any(entity.dxftype() != "LINE" for entity in entities):
        return entities
    source_polygon = validate_closed_outline(entities)
    vertices = _without_collinear_boundary_vertices(source_polygon)
    source_points = tuple(
        point
        for segment in source_segments
        if segment.entity.dxftype() == "LINE"
        for point in (segment.points[0], segment.points[-1])
    )
    if not source_points:
        return entities
    source_bounds = (
        min(point[0] for point in source_points),
        min(point[1] for point in source_points),
        max(point[0] for point in source_points),
        max(point[1] for point in source_points),
    )
    candidates: dict[
        str,
        tuple[Polygon, tuple[tuple[float, float], ...]],
    ] = {}
    for index in range(len(vertices)):
        rotated = (*vertices[index:], *vertices[:index])
        if len(rotated) < 6:
            continue
        first, second, third, fourth, fifth = rotated[:5]
        lengths = (
            dist(first, second),
            dist(second, third),
            dist(third, fourth),
            dist(fourth, fifth),
        )
        long_limit = min(lengths[0], lengths[3])
        short_total = lengths[1] + lengths[2]
        if (
            long_limit <= 1e-12
            or max(lengths[1], lengths[2])
            > long_limit * TIP_SHORT_EDGE_MAX_RATIO
            or short_total > long_limit * TIP_SHORT_CHAIN_MAX_RATIO
        ):
            continue
        previous = rotated[-1]
        following = rotated[5]
        first_sides = [(first, second)]
        second_sides = [(fourth, fifth)]
        for segment in source_segments:
            if segment.entity.dxftype() != "LINE":
                continue
            start = segment.points[0]
            end = segment.points[-1]
            for outer, tip in ((start, end), (end, start)):
                if (
                    dist(outer, tip) >= lengths[0] * 0.8
                    and _parallel_same_direction(first, second, outer, tip)
                    and _point_to_support_line(outer, previous, first)
                    <= TOPOLOGY_TOLERANCE_MM
                    and dist(outer, first)
                    <= short_total + TOPOLOGY_TOLERANCE_MM
                    and dist(tip, second)
                    <= short_total + TOPOLOGY_TOLERANCE_MM
                ):
                    _append_unique_side(first_sides, (outer, tip))
                if (
                    dist(outer, tip) >= lengths[3] * 0.8
                    and _parallel_same_direction(fourth, fifth, tip, outer)
                    and _point_to_support_line(outer, fifth, following)
                    <= TOPOLOGY_TOLERANCE_MM
                    and dist(outer, fifth)
                    <= short_total + TOPOLOGY_TOLERANCE_MM
                    and dist(tip, fourth)
                    <= short_total + TOPOLOGY_TOLERANCE_MM
                ):
                    _append_unique_side(second_sides, (tip, outer))
        for first_side in first_sides:
            for second_side in second_sides:
                intersection = _support_line_intersection(
                    *first_side,
                    *second_side,
                )
                if intersection is None:
                    continue
                if (
                    dist(intersection, first_side[1])
                    > short_total + TOPOLOGY_TOLERANCE_MM
                    or dist(intersection, second_side[0])
                    > short_total + TOPOLOGY_TOLERANCE_MM
                ):
                    continue
                completed_vertices = (
                    first_side[0],
                    intersection,
                    second_side[1],
                    *rotated[5:],
                )
                completed_polygon = Polygon(completed_vertices)
                if not completed_polygon.is_valid or completed_polygon.area <= 1e-6:
                    continue
                if completed_polygon.area + 1e-6 < source_polygon.area:
                    continue
                completed_bounds = completed_polygon.bounds
                if (
                    completed_bounds[0]
                    < source_bounds[0] - TOPOLOGY_TOLERANCE_MM
                    or completed_bounds[1]
                    < source_bounds[1] - TOPOLOGY_TOLERANCE_MM
                    or completed_bounds[2]
                    > source_bounds[2] + TOPOLOGY_TOLERANCE_MM
                    or completed_bounds[3]
                    > source_bounds[3] + TOPOLOGY_TOLERANCE_MM
                ):
                    continue
                changed_area = source_polygon.symmetric_difference(
                    completed_polygon
                ).area
                if changed_area > source_polygon.area * TIP_AREA_CHANGE_MAX_RATIO:
                    continue
                candidates[completed_polygon.normalize().wkb_hex] = (
                    completed_polygon,
                    completed_vertices,
                )
    if not candidates:
        return entities
    ranked = sorted(candidates.values(), key=lambda value: value[0].area, reverse=True)
    completed_polygon, completed_vertices = ranked[0]
    if any(
        abs(candidate.area - completed_polygon.area) <= 0.01
        and candidate.symmetric_difference(completed_polygon).area > 0.01
        for candidate, _ in ranked[1:]
    ):
        return entities
    template = cast(Line, entities[0])
    completed_entities: list[DXFEntity] = []
    for start, end in zip(
        completed_vertices,
        (*completed_vertices[1:], completed_vertices[0]),
        strict=True,
    ):
        line = cast(Line, template.copy())
        line.dxf.start = start
        line.dxf.end = end
        completed_entities.append(line)
    result = tuple(completed_entities)
    proved = validate_closed_outline(result)
    if proved.symmetric_difference(completed_polygon).area > 0.01:
        return entities
    return result


def analyze_geometry(
    context: PLSourceContext,
    metadata: PLMetadata,
) -> tuple[PlateOutline, SectionProof | None]:
    native = expand_native_segments(context.entities)
    components = _proved_components(native)
    main_candidates: list[_Component] = []
    for component in components:
        min_x, min_y, max_x, max_y = component.polygon.bounds
        x_span = float(max_x - min_x)
        y_span = float(max_y - min_y)
        if (
            component.polygon.is_valid
            and not len(component.polygon.interiors)
            and abs(y_span - metadata.width_mm)
            <= NOMINAL_WIDTH_TOLERANCE_MM + NUMERIC_EPSILON_MM
            and x_span > TOPOLOGY_TOLERANCE_MM
        ):
            main_candidates.append(component)
    if not main_candidates:
        raise PLSplitError(
            "MAIN_VIEW_MISSING",
            "没有找到板宽匹配且长度轴沿 X 方向的唯一主视图。",
        )
    if len(main_candidates) > 1:
        bom_differences = tuple(
            abs(
                float(component.polygon.bounds[2] - component.polygon.bounds[0])
                - metadata.bom_length_mm
            )
            for component in main_candidates
        )
        smallest_difference = min(bom_differences)
        preferred = [
            component
            for component, difference in zip(
                main_candidates, bom_differences, strict=True
            )
            if difference - smallest_difference <= TOPOLOGY_TOLERANCE_MM
        ]
        if len(preferred) == 1:
            main_candidates = preferred
    if len(main_candidates) != 1:
        raise PLSplitError(
            "MAIN_VIEW_AMBIGUOUS",
            f"识别到 {len(main_candidates)} 个同等可信主视图。",
        )
    main = main_candidates[0]
    contained = [
        component
        for component in components
        if component is not main
        and main.polygon.contains(component.polygon.representative_point())
    ]
    cutout_groups = [
        _outer_entities(component)
        for component in contained
        if component.polygon.area <= main.polygon.area * MAX_CONTAINED_CUTOUT_AREA_RATIO
    ]
    selected_centers: list[tuple[float, float]] = []
    bolt_circles = sorted(
        (
            cast(Circle, entity)
            for entity in context.entities
            if entity.dxftype() == "CIRCLE" and entity.dxf.layer.casefold() == "bolt"
        ),
        key=lambda circle: -float(circle.dxf.radius),
    )
    for circle in bolt_circles:
        center = Point(float(circle.dxf.center.x), float(circle.dxf.center.y))
        circle_polygon = center.buffer(float(circle.dxf.radius), quad_segs=64)
        if not main.polygon.covers(circle_polygon):
            continue
        if any(
            dist((center.x, center.y), selected) <= TOPOLOGY_TOLERANCE_MM
            for selected in selected_centers
        ):
            continue
        selected_centers.append((float(center.x), float(center.y)))
        cutout_groups.append(_circle_arc_group(circle))
    cutout_groups = list(_without_large_circle_covered_centers(tuple(cutout_groups)))
    outer_entities = _outer_entities(main)
    proved_outer = validate_closed_outline(
        outer_entities, tolerance_mm=TOPOLOGY_TOLERANCE_MM
    )
    if (
        proved_outer.symmetric_difference(main.polygon).area
        > MAIN_BOUNDARY_AREA_TOLERANCE_MM2
    ):
        raise PLSplitError(
            "MAIN_BOUNDARY_MISMATCH", "原生外边界与主视图材料区域不一致。"
        )
    native_bounds = bbox.extents(outer_entities, fast=False)
    if not native_bounds.has_data:
        raise PLSplitError("MAIN_BOUNDARY_MISSING", "主视图原生外边界没有有效范围。")
    min_x = float(native_bounds.extmin.x)
    min_y = float(native_bounds.extmin.y)
    max_x = float(native_bounds.extmax.x)
    max_y = float(native_bounds.extmax.y)
    projection = max_x - min_x
    width = max_y - min_y

    independent = tuple(
        component
        for component in components
        if component is not main and component not in contained
    )
    section_candidates: list[_Component] = []
    for component in independent:
        x_span = float(component.polygon.bounds[2] - component.polygon.bounds[0])
        if (
            component.polygon.is_valid
            and not len(component.polygon.interiors)
            and abs(x_span - projection) <= TOPOLOGY_TOLERANCE_MM
        ):
            section_candidates.append(component)
    if not section_candidates:
        if independent:
            raise PLSplitError(
                "SECTION_MISSING", "独立闭合视图不能证明普通平板或恒厚剖面。"
            )
        outer_entities = _recover_flat_curve_chains(outer_entities)
        outer_entities = _complete_flat_tip_transition(
            outer_entities,
            main.segments,
        )
        flat_polygon = validate_closed_outline(outer_entities)
        flat_bounds = bbox.extents(outer_entities, fast=False)
        if not flat_bounds.has_data:
            raise PLSplitError("MAIN_BOUNDARY_MISSING", "普通平板外边界没有有效范围。")
        min_x = float(flat_bounds.extmin.x)
        min_y = float(flat_bounds.extmin.y)
        max_x = float(flat_bounds.extmax.x)
        max_y = float(flat_bounds.extmax.y)
        return PlateOutline(
            outer_entities=outer_entities,
            polygon=flat_polygon,
            projection_length_mm=max_x - min_x,
            width_mm=max_y - min_y,
            anchor_x_mm=min_x,
            source_handles=tuple(
                _entity_handle(segment.entity) for segment in main.segments
            ),
            candidate_count=len(main_candidates),
            cutout_entity_groups=tuple(cutout_groups),
        ), None
    if len(section_candidates) != 1:
        normalized = tuple(
            translate(
                component.polygon,
                xoff=-component.polygon.bounds[0],
                yoff=-component.polygon.bounds[1],
            )
            for component in section_candidates
        )
        reference = normalized[0]
        if any(
            reference.symmetric_difference(candidate).area > 0.01
            for candidate in normalized[1:]
        ):
            raise PLSplitError(
                "SECTION_AMBIGUOUS",
                f"识别到 {len(section_candidates)} 个同等可信剖面。",
            )
    section = section_candidates[0]
    k_length = float(section.polygon.area / metadata.thickness_mm)
    if k_length <= 0.0:
        raise PLSplitError("SECTION_INVALID", "剖面材料面积不能证明正的中面长度。")
    outline = PlateOutline(
        outer_entities=outer_entities,
        polygon=main.polygon,
        projection_length_mm=projection,
        width_mm=width,
        anchor_x_mm=float(min_x),
        source_handles=tuple(
            _entity_handle(segment.entity) for segment in main.segments
        ),
        candidate_count=len(main_candidates),
        cutout_entity_groups=tuple(cutout_groups),
    )
    section_proof = SectionProof(
        polygon=section.polygon,
        k_length_mm=k_length,
        equivalent_surface_lengths_mm=(k_length, k_length),
        proof_method="section_area_over_thickness_k_half",
        source_handles=tuple(
            _entity_handle(segment.entity) for segment in section.segments
        ),
        candidate_count=len(section_candidates),
    )
    return outline, section_proof
