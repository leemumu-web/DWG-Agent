from __future__ import annotations

from dataclasses import dataclass
from math import dist, radians
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
MAX_CONTAINED_CUTOUT_AREA_RATIO = 0.1


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


def analyze_geometry(
    context: PLSourceContext,
    metadata: PLMetadata,
) -> tuple[PlateOutline, SectionProof]:
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
            and abs(y_span - metadata.width_mm) <= NOMINAL_WIDTH_TOLERANCE_MM
            and x_span > TOPOLOGY_TOLERANCE_MM
        ):
            main_candidates.append(component)
    if not main_candidates:
        raise PLSplitError(
            "MAIN_VIEW_MISSING",
            "没有找到板宽匹配且长度轴沿 X 方向的唯一主视图。",
        )
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
    outer_entities = _outer_entities(main)
    proved_outer = validate_closed_outline(
        outer_entities, tolerance_mm=TOPOLOGY_TOLERANCE_MM
    )
    if proved_outer.symmetric_difference(main.polygon).area > 0.01:
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

    section_candidates: list[_Component] = []
    for component in components:
        if component is main or component in contained:
            continue
        x_span = float(component.polygon.bounds[2] - component.polygon.bounds[0])
        if (
            component.polygon.is_valid
            and not len(component.polygon.interiors)
            and abs(x_span - projection) <= TOPOLOGY_TOLERANCE_MM
        ):
            section_candidates.append(component)
    if not section_candidates:
        raise PLSplitError(
            "SECTION_MISSING", "没有找到与主视图投影对应的闭合恒厚剖面。"
        )
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
