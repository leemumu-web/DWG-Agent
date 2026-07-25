from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import cos, hypot, pi, sin
import re
from typing import Any, Iterable

from shapely.geometry import LineString

from .bh_bolt_semantics import opening_nominal_width, polygonize_closed_bolt_linework
from .bh_canonical import canonical_sha256
from .bh_dialect import BHDialectProfile, DEFAULT_TEKLA_DIALECT
from .bh_dimensions import dimension_property_type, displayed_dimension_tolerance
from .bh_frames import LocalFrame
from .bh_ir import SemanticLayer
from .bh_regions import NormalizedEntity, RegionBuildResult, ViewRegion
from .bh_source import SourceDocument
from .geometry_types import BoundingBox, Point2D


_NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
_CHAIN_RE = re.compile(r"^(?P<count>\d+)\s*[xX×*]\s*(?P<pitch>\d+(?:\.\d+)?)$")
_PROFILE_RE = re.compile(
    r"(?i)(?<![A-Z0-9])BH\s*\d+(?:\.\d+)?"
    r"(?:\s*[-~～—]\s*\d+(?:\.\d+)?)?\s*[xX×*]\s*"
    r"\d+(?:\.\d+)?\s*[xX×*]\s*\d+(?:\.\d+)?"
    r"\s*[xX×*]\s*\d+(?:\.\d+)?"
)
_PART_RE = re.compile(r"(?i)^[a-z0-9]+(?:-[a-z0-9]+)+$")
_MATERIAL_RE = re.compile(r"(?i)^Q\d{3}[A-Z0-9-]*$")
_SCALE_RE = re.compile(r"(?<!\d)1\s*:\s*\d+(?:\.\d+)?")


class DrawingNodeKind(str, Enum):
    PRIMITIVE = "primitive"
    TEXT_TOKEN = "text_token"
    DIMENSION = "dimension"
    VIEW_REGION = "view_region"
    METADATA_ROW = "metadata_row"
    PART_MARK = "part_mark"
    BOLT_MARK = "bolt_mark"
    SECTION_SYMBOL = "section_symbol"
    PHYSICAL_OPENING = "physical_opening"


class DrawingEdgeKind(str, Enum):
    CONTAINS = "contains"
    MEASURES = "measures"
    HAS_TOKEN = "has_token"
    ALIGNED_WITH = "aligned_with"
    LABELS = "labels"
    PROJECTS_TO = "projects_to"


class AssociationStrength(str, Enum):
    EXPLICIT = "explicit"
    GEOMETRIC = "geometric"
    CANDIDATE = "candidate"


@dataclass(frozen=True, slots=True)
class DrawingNode:
    node_id: str
    kind: DrawingNodeKind
    source_ids: tuple[str, ...]
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "kind": self.kind.value,
            "source_ids": list(self.source_ids),
            "attributes": self.attributes,
        }


@dataclass(frozen=True, slots=True)
class DrawingEdge:
    edge_id: str
    source: str
    relation: DrawingEdgeKind
    target: str
    source_ids: tuple[str, ...]
    rule_id: str
    residual_mm: float
    strength: AssociationStrength
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "relation": self.relation.value,
            "target": self.target,
            "source_ids": list(self.source_ids),
            "rule_id": self.rule_id,
            "residual_mm": self.residual_mm,
            "strength": self.strength.value,
            "attributes": self.attributes,
        }


@dataclass(slots=True)
class DrawingGraph:
    nodes: list[DrawingNode] = field(default_factory=list)
    edges: list[DrawingEdge] = field(default_factory=list)

    def add_node(
        self,
        kind: DrawingNodeKind,
        *,
        source_ids: Iterable[str] = (),
        attributes: dict[str, Any] | None = None,
    ) -> DrawingNode:
        sources = tuple(sorted(set(source_ids)))
        # Own the attribute mapping used to derive the canonical ID.  Callers
        # must not be able to mutate a node after its ID has been computed.
        values = dict(attributes or {})
        node_id = f"drawing:{kind.value}:" + canonical_sha256(
            {"kind": kind.value, "source_ids": sources, "attributes": values}
        )[:24]
        node = DrawingNode(node_id, kind, sources, values)
        if not any(item.node_id == node_id for item in self.nodes):
            self.nodes.append(node)
        return next(item for item in self.nodes if item.node_id == node_id)

    def add_edge(
        self,
        source: DrawingNode,
        relation: DrawingEdgeKind,
        target: DrawingNode,
        *,
        source_ids: Iterable[str],
        rule_id: str,
        residual_mm: float,
        strength: AssociationStrength,
        attributes: dict[str, Any] | None = None,
    ) -> DrawingEdge:
        sources = tuple(sorted(set(source_ids)))
        values = dict(attributes or {})
        payload = {
            "source": source.node_id,
            "relation": relation.value,
            "target": target.node_id,
            "source_ids": sources,
            "rule_id": rule_id,
            "residual_mm": round(float(residual_mm), 9),
            "strength": strength.value,
            "attributes": values,
        }
        edge = DrawingEdge(
            edge_id="drawing-edge:" + canonical_sha256(payload)[:24],
            source=source.node_id,
            relation=relation,
            target=target.node_id,
            source_ids=sources,
            rule_id=rule_id,
            residual_mm=round(float(residual_mm), 9),
            strength=strength,
            attributes=values,
        )
        if not any(item.edge_id == edge.edge_id for item in self.edges):
            self.edges.append(edge)
        return edge

    def nodes_of(self, kind: DrawingNodeKind) -> list[DrawingNode]:
        return sorted(
            (node for node in self.nodes if node.kind == kind),
            key=lambda node: node.node_id,
        )

    def edges_of(self, relation: DrawingEdgeKind) -> list[DrawingEdge]:
        return sorted(
            (edge for edge in self.edges if edge.relation == relation),
            key=lambda edge: edge.edge_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [item.to_dict() for item in sorted(self.nodes, key=lambda item: item.node_id)],
            "edges": [item.to_dict() for item in sorted(self.edges, key=lambda item: item.edge_id)],
        }


def _bbox_payload(bbox: BoundingBox | None) -> list[float] | None:
    if bbox is None:
        return None
    return [bbox.min_x, bbox.min_y, bbox.max_x, bbox.max_y]


def _entity_kind(entity: NormalizedEntity) -> DrawingNodeKind:
    if entity.entity_type == "DIMENSION":
        return DrawingNodeKind.DIMENSION
    if entity.text is not None:
        return DrawingNodeKind.TEXT_TOKEN
    return DrawingNodeKind.PRIMITIVE


def _is_association_fact(entity: NormalizedEntity) -> bool:
    """Keep facts which can participate in an engineering relation.

    The lossless SourceIR remains the source of truth for every DXF entity.
    The drawing graph deliberately excludes page furniture and unclassified
    non-text decoration so that a border line is not promoted to a semantic
    primitive merely because it exists in the source file.
    """

    if entity.text is not None or entity.entity_type == "DIMENSION":
        return True
    return entity.semantic_role not in {
        SemanticLayer.DRAWING_SHEET,
        SemanticLayer.OTHER,
        SemanticLayer.UNKNOWN,
    }


def _position(entity: NormalizedEntity) -> Point2D | None:
    if entity.geometry is None or not entity.geometry.coordinates:
        return None
    return Point2D(*entity.geometry.coordinates[0])


def _line_points(entity: NormalizedEntity) -> tuple[Point2D, Point2D] | None:
    if (
        entity.entity_type != "LINE"
        or entity.geometry is None
        or len(entity.geometry.coordinates) != 2
    ):
        return None
    return Point2D(*entity.geometry.coordinates[0]), Point2D(*entity.geometry.coordinates[1])


def _contains(bbox: BoundingBox, point: Point2D, tolerance: float = 1.0) -> bool:
    expanded = bbox.expanded(tolerance)
    return (
        expanded.min_x <= point.x <= expanded.max_x
        and expanded.min_y <= point.y <= expanded.max_y
    )


def _point_segment_distance(point: Point2D, start: Point2D, end: Point2D) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    length2 = dx * dx + dy * dy
    if length2 <= 1e-18:
        return point.distance_to(start)
    parameter = max(
        0.0,
        min(1.0, ((point.x - start.x) * dx + (point.y - start.y) * dy) / length2),
    )
    projection = Point2D(start.x + parameter * dx, start.y + parameter * dy)
    return point.distance_to(projection)


def _point_line_distance(point: Point2D, start: Point2D, end: Point2D) -> float:
    dx = end.x - start.x
    dy = end.y - start.y
    length = start.distance_to(end)
    if length <= 1e-12:
        return point.distance_to(start)
    return abs(dx * (start.y - point.y) - (start.x - point.x) * dy) / length


def _point_bbox_distance(point: Point2D, bbox: BoundingBox) -> float:
    dx = max(bbox.min_x - point.x, 0.0, point.x - bbox.max_x)
    dy = max(bbox.min_y - point.y, 0.0, point.y - bbox.max_y)
    return hypot(dx, dy)


def _segment_intersects_bbox(
    start: Point2D,
    end: Point2D,
    bbox: BoundingBox,
) -> bool:
    """Liang-Barsky intersection against an axis-aligned view envelope."""

    dx = end.x - start.x
    dy = end.y - start.y
    lower = 0.0
    upper = 1.0
    for p, q in (
        (-dx, start.x - bbox.min_x),
        (dx, bbox.max_x - start.x),
        (-dy, start.y - bbox.min_y),
        (dy, bbox.max_y - start.y),
    ):
        if abs(p) <= 1e-18:
            if q < 0.0:
                return False
            continue
        ratio = q / p
        if p < 0.0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _segment_bbox_distance(
    start: Point2D,
    end: Point2D,
    bbox: BoundingBox,
) -> float:
    if _segment_intersects_bbox(start, end, bbox):
        return 0.0
    corners = (
        Point2D(bbox.min_x, bbox.min_y),
        Point2D(bbox.min_x, bbox.max_y),
        Point2D(bbox.max_x, bbox.min_y),
        Point2D(bbox.max_x, bbox.max_y),
    )
    return min(
        _point_bbox_distance(start, bbox),
        _point_bbox_distance(end, bbox),
        *(_point_segment_distance(corner, start, end) for corner in corners),
    )


def _view_node_for_region(
    graph: DrawingGraph,
    region: ViewRegion,
) -> DrawingNode:
    return graph.add_node(
        DrawingNodeKind.VIEW_REGION,
        source_ids=region.source_ids,
        attributes={
            "region_id": region.region_id,
            "geometry_signature": region.geometry_signature,
            "bbox_mm": _bbox_payload(region.bbox),
            "explicit_block": region.explicit_block,
        },
    )


def _add_explicit_dimensions(
    graph: DrawingGraph,
    source: SourceDocument,
    normalized: dict[str, NormalizedEntity],
    view_nodes: list[tuple[ViewRegion, DrawingNode]],
    entity_nodes: dict[str, DrawingNode],
) -> None:
    for entity in source.entities:
        if entity.entity_type != "DIMENSION" or entity.dimension_measurement is None:
            continue
        current = normalized[entity.source_id]
        geometry = current.geometry
        if geometry is None or len(geometry.coordinates) < 3:
            continue
        first = Point2D(*geometry.coordinates[1])
        second = Point2D(*geometry.coordinates[2])
        derived = first.distance_to(second)
        orientation = "horizontal" if abs(second.x - first.x) >= abs(second.y - first.y) else "vertical"
        node = entity_nodes[entity.source_id]
        for region, view_node in view_nodes:
            if not (_contains(region.bbox, first) and _contains(region.bbox, second)):
                continue
            residual = abs(entity.dimension_measurement - derived)
            view_residual = abs(
                derived
                - (
                    region.bbox.width
                    if orientation == "horizontal"
                    else region.bbox.height
                )
            )
            scope = "view_extent" if view_residual <= 1.0 else "partial_or_untyped"
            property_type = dimension_property_type(scope, orientation)
            graph.add_edge(
                node,
                DrawingEdgeKind.MEASURES,
                view_node,
                source_ids=(entity.source_id,),
                rule_id="DXF.DIMENSION.DEFINITION_POINTS",
                residual_mm=residual,
                strength=AssociationStrength.EXPLICIT,
                attributes={
                    "definition_points": [[first.x, first.y], [second.x, second.y]],
                    "orientation": orientation,
                    "scope": scope,
                    "property_type": property_type,
                    "view_envelope_residual_mm": round(view_residual, 9),
                },
            )


def _add_physical_cut_projections(
    graph: DrawingGraph,
    normalized_entities: tuple[NormalizedEntity, ...],
    view_nodes: list[tuple[ViewRegion, DrawingNode]],
    entity_nodes: dict[str, DrawingNode],
) -> None:
    """Join each source Bolt/CIRCLE fact to compatible drawing views."""

    for entity in normalized_entities:
        if (
            entity.semantic_role != SemanticLayer.PHYSICAL_CUT
            or entity.entity_type != "CIRCLE"
            or entity.geometry is None
            or entity.geometry.center is None
            or entity.geometry.radius is None
            or entity.source_id not in entity_nodes
        ):
            continue
        center = Point2D(*entity.geometry.center)
        maximum_gap = entity.geometry.radius + 1.0
        for region, view_node in view_nodes:
            residual = _point_bbox_distance(center, region.bbox)
            if residual > maximum_gap:
                continue
            graph.add_edge(
                entity_nodes[entity.source_id],
                DrawingEdgeKind.PROJECTS_TO,
                view_node,
                source_ids=(entity.source_id,),
                rule_id="BH.PHYSICAL_CUT.CENTER_IN_VIEW",
                residual_mm=residual,
                strength=AssociationStrength.GEOMETRIC,
                attributes={
                    "region_id": region.region_id,
                    "center_mm": [center.x, center.y],
                    "radius_mm": entity.geometry.radius,
                },
            )


def _add_polygonal_cut_groups(
    graph: DrawingGraph,
    normalized_entities: tuple[NormalizedEntity, ...],
    view_nodes: list[tuple[ViewRegion, DrawingNode]],
    entity_nodes: dict[str, DrawingNode],
) -> list[DrawingNode]:
    """Promote complete Bolt/LINE rings from helpers to physical cut objects."""

    grouped: dict[str, list[tuple[NormalizedEntity, LineString]]] = {}
    for entity in normalized_entities:
        if (
            entity.semantic_role != SemanticLayer.CUT_HELPER
            or entity.entity_type != "LINE"
            or entity.geometry is None
            or len(entity.geometry.coordinates) < 2
        ):
            continue
        start, end = entity.geometry.coordinates[:2]
        grouped.setdefault(entity.container_id, []).append(
            (entity, LineString((start, end)))
        )

    result: list[DrawingNode] = []
    for container_id, rows in sorted(grouped.items()):
        for polygon in polygonize_closed_bolt_linework(line for _, line in rows):
            boundary_band = polygon.boundary.buffer(0.05)
            sources = tuple(
                sorted(
                    entity.source_id
                    for entity, line in rows
                    if boundary_band.covers(line)
                )
            )
            if not sources:
                continue
            min_x, min_y, max_x, max_y = polygon.bounds
            center = polygon.centroid
            node = graph.add_node(
                DrawingNodeKind.PHYSICAL_OPENING,
                source_ids=sources,
                attributes={
                    "container_id": container_id,
                    "semantic_role": SemanticLayer.PHYSICAL_CUT.value,
                    "geometry_kind": "polygonal_opening",
                    "center_mm": [center.x, center.y],
                    "bbox_mm": [min_x, min_y, max_x, max_y],
                    "nominal_diameter_mm": opening_nominal_width(polygon),
                    "area_mm2": polygon.area,
                },
            )
            result.append(node)
            for source_id in sources:
                if source_id not in entity_nodes:
                    continue
                graph.add_edge(
                    node,
                    DrawingEdgeKind.CONTAINS,
                    entity_nodes[source_id],
                    source_ids=(source_id,),
                    rule_id="TEKLA.BOLT_LINE.CLOSED_BOUNDARY",
                    residual_mm=0.0,
                    strength=AssociationStrength.GEOMETRIC,
                )
            for region, view_node in view_nodes:
                residual = _point_bbox_distance(
                    Point2D(center.x, center.y), region.bbox
                )
                if residual > 1.0:
                    continue
                graph.add_edge(
                    node,
                    DrawingEdgeKind.PROJECTS_TO,
                    view_node,
                    source_ids=sources,
                    rule_id="BH.PHYSICAL_CUT.CLOSED_BOLT_LINES_IN_VIEW",
                    residual_mm=residual,
                    strength=AssociationStrength.GEOMETRIC,
                    attributes={"region_id": region.region_id},
                )
    return result


def _matching_extension_lines(
    endpoint: Point2D,
    lines: list[tuple[NormalizedEntity, Point2D, Point2D]],
    region: ViewRegion,
    *,
    dimension_start: Point2D,
    dimension_end: Point2D,
    maximum_origin_gap_mm: float,
) -> list[tuple[NormalizedEntity, float]]:
    matches: list[tuple[NormalizedEntity, float]] = []
    dimension_dx = dimension_end.x - dimension_start.x
    dimension_dy = dimension_end.y - dimension_start.y
    dimension_length = hypot(dimension_dx, dimension_dy)
    for entity, start, end in lines:
        if _point_segment_distance(endpoint, start, end) > 1.0:
            continue
        extension_dx = end.x - start.x
        extension_dy = end.y - start.y
        extension_length = hypot(extension_dx, extension_dy)
        if dimension_length <= 1e-18 or extension_length <= 1e-18:
            continue
        perpendicular_cosine = abs(
            dimension_dx * extension_dx + dimension_dy * extension_dy
        ) / (dimension_length * extension_length)
        # Tekla extension lines are perpendicular to the dimension line.  This
        # rejects the two diagonal arrowhead strokes without using their size.
        if perpendicular_cosine > 0.10:
            continue
        origin_gap = _segment_bbox_distance(start, end, region.bbox)
        if origin_gap > maximum_origin_gap_mm:
            continue
        matches.append((entity, origin_gap))
    return sorted(matches, key=lambda item: (item[1], item[0].source_id))


def _add_exploded_dimensions(
    graph: DrawingGraph,
    source: SourceDocument,
    normalized_entities: tuple[NormalizedEntity, ...],
    view_nodes: list[tuple[ViewRegion, DrawingNode]],
    dialect: BHDialectProfile,
) -> None:
    already_bound = {
        source_id
        for node in graph.nodes_of(DrawingNodeKind.DIMENSION)
        for source_id in node.source_ids
    }
    for items in _annotation_role_groups(
        source,
        normalized_entities,
        SemanticLayer.DIMENSION,
    ):
        lines = [
            (entity, *points)
            for entity in items
            if (points := _line_points(entity)) is not None
        ]
        dimension_texts = [
            entity
            for entity in items
            if entity.normalized_text is not None
            and (
                _NUMBER_RE.fullmatch(entity.normalized_text)
                or _CHAIN_RE.fullmatch(entity.normalized_text)
            )
            and _position(entity) is not None
        ]
        for text_entity in dimension_texts:
            if text_entity.source_id in already_bound:
                continue
            text = text_entity.normalized_text or ""
            chain = _CHAIN_RE.fullmatch(text)
            value = None if chain else float(text)
            chain_interval_count = int(chain.group("count")) if chain else None
            chain_pitch = float(chain.group("pitch")) if chain else None
            measured_length = chain_pitch if chain_pitch is not None else value
            assert measured_length is not None
            precision_text = chain.group("pitch") if chain else text
            tolerance = displayed_dimension_tolerance(
                precision_text,
            )
            maximum_origin_gap = max(
                1.0,
                (text_entity.text_height or 0.0)
                * dialect.dimension_origin_offset_text_ratio
                + dialect.dimension_origin_offset_tolerance_mm,
            )
            line_candidates = []
            text_position = _position(text_entity)
            assert text_position is not None
            for line_entity, start, end in lines:
                length = start.distance_to(end)
                residual = abs(length - measured_length)
                if residual > tolerance:
                    continue
                midpoint = Point2D((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)
                line_candidates.append(
                    (residual, text_position.distance_to(midpoint), line_entity, start, end)
                )
            associations = []
            for residual, text_distance, line_entity, start, end in sorted(
                line_candidates,
                key=lambda item: (item[1], item[0], item[2].source_id),
            ):
                other_lines = [item for item in lines if item[0] is not line_entity]
                for region, view_node in view_nodes:
                    first_extensions = _matching_extension_lines(
                        start,
                        other_lines,
                        region,
                        dimension_start=start,
                        dimension_end=end,
                        maximum_origin_gap_mm=maximum_origin_gap,
                    )
                    second_extensions = _matching_extension_lines(
                        end,
                        other_lines,
                        region,
                        dimension_start=start,
                        dimension_end=end,
                        maximum_origin_gap_mm=maximum_origin_gap,
                    )
                    if not first_extensions or not second_extensions:
                        continue
                    orientation = (
                        "horizontal"
                        if abs(end.x - start.x) >= abs(end.y - start.y)
                        else "vertical"
                    )
                    view_residual = abs(
                        measured_length
                        - (
                            region.bbox.width
                            if orientation == "horizontal"
                            else region.bbox.height
                        )
                    )
                    first_extension, first_gap = first_extensions[0]
                    second_extension, second_gap = second_extensions[0]
                    origin_gap = max(first_gap, second_gap)
                    associations.append(
                        (
                            residual,
                            text_distance,
                            origin_gap,
                            first_gap + second_gap,
                            view_residual,
                            region.region_id,
                            line_entity,
                            start,
                            end,
                            first_extension,
                            second_extension,
                            view_node,
                        )
                    )
            if associations:
                (
                    residual,
                    _,
                    origin_gap,
                    _,
                    view_residual,
                    _,
                    line_entity,
                    start,
                    end,
                    first_extension,
                    second_extension,
                    view_node,
                ) = min(
                    associations,
                    key=lambda item: (
                        item[1],
                        item[2],
                        item[3],
                        item[0],
                        item[4],
                        item[5],
                    ),
                )
                scope = (
                    "pitch_chain"
                    if chain is not None
                    else "view_extent"
                    if view_residual <= tolerance
                    else "partial_or_untyped"
                )
                orientation = (
                    "horizontal"
                    if abs(end.x - start.x) >= abs(end.y - start.y)
                    else "vertical"
                )
                property_type = dimension_property_type(scope, orientation)
                sources = (
                    text_entity.source_id,
                    line_entity.source_id,
                    first_extension.source_id,
                    second_extension.source_id,
                )
                node = graph.add_node(
                    DrawingNodeKind.DIMENSION,
                    source_ids=sources,
                    attributes={
                        "representation": "exploded",
                        "value_mm": value,
                        "text": text,
                        "chain_interval_count": chain_interval_count,
                        "chain_pitch_mm": chain_pitch,
                        "orientation": orientation,
                        "scope": scope,
                        "property_type": property_type,
                        "origin_gap_mm": round(origin_gap, 9),
                        "view_envelope_residual_mm": round(view_residual, 9),
                    },
                )
                graph.add_edge(
                    node,
                    DrawingEdgeKind.MEASURES,
                    view_node,
                    source_ids=sources,
                    rule_id="TEKLA.DIMENSION.EXTENSION_CHAIN",
                    residual_mm=residual,
                    strength=AssociationStrength.GEOMETRIC,
                    attributes={
                        "scope": scope,
                        "orientation": orientation,
                        "property_type": property_type,
                        "origin_gap_mm": round(origin_gap, 9),
                        "view_envelope_residual_mm": round(view_residual, 9),
                    },
                )


def _group_bbox(items: list[NormalizedEntity]) -> BoundingBox | None:
    boxes = [entity.bbox for entity in items if entity.bbox is not None]
    if not boxes:
        return None
    return BoundingBox(
        min(box.min_x for box in boxes),
        min(box.min_y for box in boxes),
        max(box.max_x for box in boxes),
        max(box.max_y for box in boxes),
    )


def _ungrouped_annotation_objects(
    role: SemanticLayer,
    items: list[NormalizedEntity],
) -> list[list[NormalizedEntity]]:
    """Recover Tekla annotation objects after export grouping is exploded.

    Bolt and part mark leaders are connected polylines.  Endpoint-connected
    components are stable under translation/reflection and do not depend on
    text values or sample names.  Section pairs are intentionally kept as one
    container-level object because their parallel cut lines need not touch.
    """

    if role == SemanticLayer.SECTION:
        return [items]
    lines = [
        (entity, points)
        for entity in items
        if (points := _line_points(entity)) is not None
    ]
    if len(lines) <= 1:
        return [items]
    parents = list(range(len(lines)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = root(first)
        second_root = root(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for first_index, (_, first_points) in enumerate(lines):
        for second_index, (_, second_points) in enumerate(lines[:first_index]):
            endpoint_gap = min(
                first.distance_to(second)
                for first in first_points
                for second in second_points
            )
            if endpoint_gap <= 1.0:
                union(first_index, second_index)
    component_indexes: dict[int, list[int]] = {}
    for index in range(len(lines)):
        component_indexes.setdefault(root(index), []).append(index)
    if len(component_indexes) == 1:
        return [items]
    components = [
        [lines[index][0] for index in indexes]
        for _, indexes in sorted(
            component_indexes.items(),
            key=lambda item: min(lines[index][0].source_id for index in item[1]),
        )
    ]
    line_source_ids = {entity.source_id for entity, _ in lines}
    for entity in items:
        if entity.source_id in line_source_ids:
            continue
        point = _position(entity)
        if point is None and entity.bbox is not None:
            point = Point2D(
                (entity.bbox.min_x + entity.bbox.max_x) / 2.0,
                (entity.bbox.min_y + entity.bbox.max_y) / 2.0,
            )
        if point is None:
            components[0].append(entity)
            continue
        _, component_index = min(
            (
                min(
                    _point_segment_distance(point, *points)
                    for line in component
                    if (points := _line_points(line)) is not None
                ),
                index,
            )
            for index, component in enumerate(components)
        )
        components[component_index].append(entity)
    return [
        sorted(component, key=lambda entity: entity.source_id)
        for component in components
    ]


def _annotation_role_groups(
    source: SourceDocument,
    normalized_entities: tuple[NormalizedEntity, ...],
    role: SemanticLayer,
    *,
    partition_dimensions: bool = False,
) -> list[list[NormalizedEntity]]:
    """Return role-local candidate pools at the correct grammar boundary.

    Marks have connected leaders and can be reconstructed as independent
    objects after EXPLODE.  Dimension extension lines from independent objects
    may cross or share origins, so dimension productions require the complete
    container pool and enforce their own one-object ownership.
    """

    by_container: dict[str, list[NormalizedEntity]] = {}
    for entity in normalized_entities:
        if entity.semantic_role == role:
            by_container.setdefault(entity.container_id, []).append(entity)
    explicit_containers = {
        container.container_id
        for container in source.containers
        if container.explicit_block
    }
    result: list[list[NormalizedEntity]] = []
    for container_id, items in sorted(by_container.items()):
        if role == SemanticLayer.DIMENSION and not partition_dimensions:
            result.append(sorted(items, key=lambda entity: entity.source_id))
            continue
        partitions = (
            [items]
            if container_id in explicit_containers
            else _ungrouped_annotation_objects(role, items)
        )
        result.extend(partitions)
    return result


def _add_pattern_callout_dimensions(
    graph: DrawingGraph,
    source: SourceDocument,
    normalized_entities: tuple[NormalizedEntity, ...],
    view_nodes: list[tuple[ViewRegion, DrawingNode]],
    dialect: BHDialectProfile,
    entity_nodes: dict[str, DrawingNode],
) -> None:
    """Bind Tekla ``count x pitch`` one-sided leader callouts.

    Unlike an exploded linear dimension, this representation has a pitch-sized
    orientation stroke and only one leader reaching the represented view.  It
    is intentionally a separate production rule so one-sided numeric leaders
    can never be mistaken for overall dimensions.
    """

    already_bound = {
        source_id
        for node in graph.nodes_of(DrawingNodeKind.DIMENSION)
        for source_id in node.source_ids
    }
    for items in _annotation_role_groups(
        source,
        normalized_entities,
        SemanticLayer.DIMENSION,
        partition_dimensions=True,
    ):
        claimed_pattern_line_ids: set[str] = set()
        lines = [
            (entity, *points)
            for entity in items
            if (points := _line_points(entity)) is not None
        ]
        for text_entity in items:
            text = text_entity.normalized_text or ""
            chain = _CHAIN_RE.fullmatch(text)
            text_position = _position(text_entity)
            if (
                chain is None
                or text_position is None
                or text_entity.source_id in already_bound
            ):
                continue
            pitch_text = chain.group("pitch")
            pitch = float(pitch_text)
            tolerance = displayed_dimension_tolerance(pitch_text)
            maximum_origin_gap = max(
                1.0,
                (text_entity.text_height or 0.0)
                * dialect.dimension_origin_offset_text_ratio
                + dialect.dimension_origin_offset_tolerance_mm,
            )

            def leader_anchor_candidates(
                leader: NormalizedEntity,
                view_node: DrawingNode,
            ) -> list[
                tuple[
                    float,
                    float,
                    str,
                    Point2D,
                    Point2D,
                    NormalizedEntity,
                ]
            ]:
                leader_points = _line_points(leader)
                if leader_points is None:
                    return []
                projected_cut_node_ids = {
                    edge.source
                    for edge in graph.edges_of(DrawingEdgeKind.PROJECTS_TO)
                    if edge.target == view_node.node_id
                }
                candidates = []
                for cut in normalized_entities:
                    if (
                        cut.source_id not in entity_nodes
                        or entity_nodes[cut.source_id].node_id
                        not in projected_cut_node_ids
                        or cut.geometry is None
                        or cut.geometry.center is None
                        or cut.geometry.radius is None
                    ):
                        continue
                    center = Point2D(*cut.geometry.center)
                    line_residual = _point_line_distance(
                        center,
                        leader_points[0],
                        leader_points[1],
                    )
                    leader_gap = _point_segment_distance(
                        center,
                        leader_points[0],
                        leader_points[1],
                    )
                    if (
                        line_residual > tolerance
                        or leader_gap > maximum_origin_gap
                    ):
                        continue
                    endpoint = min(
                        leader_points,
                        key=lambda point: point.distance_to(center),
                    )
                    candidates.append(
                        (
                            leader_gap,
                            line_residual,
                            cut.source_id,
                            endpoint,
                            center,
                            cut,
                        )
                    )
                return sorted(candidates, key=lambda item: item[:3])

            associations = []
            for pitch_line, start, end in lines:
                if pitch_line.source_id in claimed_pattern_line_ids:
                    continue
                residual = abs(start.distance_to(end) - pitch)
                if residual > tolerance:
                    continue
                midpoint = Point2D(
                    (start.x + end.x) / 2.0,
                    (start.y + end.y) / 2.0,
                )
                text_distance = text_position.distance_to(midpoint)
                for region, view_node in view_nodes:
                    leaders = []
                    for leader, leader_start, leader_end in lines:
                        if (
                            leader is pitch_line
                            or leader.source_id in claimed_pattern_line_ids
                        ):
                            continue
                        connection_residual = min(
                            _point_segment_distance(start, leader_start, leader_end),
                            _point_segment_distance(end, leader_start, leader_end),
                        )
                        if connection_residual > 1.0:
                            continue
                        view_gap = _segment_bbox_distance(
                            leader_start,
                            leader_end,
                            region.bbox,
                        )
                        if view_gap > maximum_origin_gap:
                            continue
                        anchor_candidates = leader_anchor_candidates(
                            leader,
                            view_node,
                        )
                        leaders.append(
                            (
                                0 if anchor_candidates else 1,
                                view_gap,
                                connection_residual,
                                -leader_start.distance_to(leader_end),
                                leader.source_id,
                                leader,
                                anchor_candidates,
                            )
                        )
                    if not leaders:
                        continue
                    (
                        _,
                        view_gap,
                        connection_residual,
                        _,
                        _,
                        leader,
                        anchor_candidates,
                    ) = min(leaders)
                    associations.append(
                        (
                            residual,
                            text_distance,
                            view_gap,
                            connection_residual,
                            region.region_id,
                            pitch_line,
                            start,
                            end,
                            leader,
                            view_node,
                            anchor_candidates,
                        )
                    )
            if not associations:
                continue
            (
                residual,
                _,
                view_gap,
                _,
                _,
                pitch_line,
                start,
                end,
                leader,
                view_node,
                anchor_candidates,
            ) = min(
                associations,
                key=lambda item: (
                    0 if item[10] else 1,
                    item[1],
                    item[3],
                    item[2],
                    item[0],
                    item[4],
                ),
            )
            sources = (
                text_entity.source_id,
                pitch_line.source_id,
                leader.source_id,
            )
            claimed_pattern_line_ids.update(sources[1:])
            anchor = anchor_candidates[0] if anchor_candidates else None
            orientation = (
                "horizontal"
                if abs(end.x - start.x) >= abs(end.y - start.y)
                else "vertical"
            )
            anchor_axis_values = [
                item[4].x if orientation == "horizontal" else item[4].y
                for item in anchor_candidates
            ]
            node = graph.add_node(
                DrawingNodeKind.DIMENSION,
                source_ids=sources,
                attributes={
                    "representation": "exploded_pattern_callout",
                    "value_mm": None,
                    "text": text,
                    "chain_interval_count": int(chain.group("count")),
                    "chain_pitch_mm": pitch,
                    "orientation": orientation,
                    "scope": "pitch_chain",
                    "property_type": "equal_pitch_chain",
                    "origin_gap_mm": round(view_gap, 9),
                    "view_envelope_residual_mm": None,
                    "leader_anchor_mm": (
                        [anchor[3].x, anchor[3].y] if anchor is not None else None
                    ),
                    "anchor_axis_mm": (
                        sum(anchor_axis_values) / len(anchor_axis_values)
                        if anchor_axis_values
                        else None
                    ),
                },
            )
            graph.add_edge(
                node,
                DrawingEdgeKind.MEASURES,
                view_node,
                source_ids=sources,
                rule_id="TEKLA.DIMENSION.PATTERN_CALLOUT",
                residual_mm=residual,
                strength=AssociationStrength.GEOMETRIC,
                attributes={
                    "scope": "pitch_chain",
                    "orientation": orientation,
                    "property_type": "equal_pitch_chain",
                    "origin_gap_mm": round(view_gap, 9),
                    "leader_anchor_mm": (
                        [anchor[3].x, anchor[3].y] if anchor is not None else None
                    ),
                },
            )
            for leader_gap, line_residual, _, endpoint, center, cut in anchor_candidates:
                graph.add_edge(
                    node,
                    DrawingEdgeKind.ALIGNED_WITH,
                    entity_nodes[cut.source_id],
                    source_ids=(*sources, cut.source_id),
                    rule_id="TEKLA.DIMENSION.PATTERN_START_CUT",
                    residual_mm=leader_gap,
                    strength=AssociationStrength.GEOMETRIC,
                    attributes={
                        "region_id": view_node.attributes["region_id"],
                        "leader_anchor_mm": [endpoint.x, endpoint.y],
                        "cut_center_mm": [center.x, center.y],
                        "cut_radius_mm": cut.geometry.radius,
                        "maximum_origin_gap_mm": maximum_origin_gap,
                        "axis_alignment_residual_mm": line_residual,
                        "anchor_axis_mm": (
                            center.x if orientation == "horizontal" else center.y
                        ),
                    },
                )


def _add_composite_annotations(
    graph: DrawingGraph,
    source: SourceDocument,
    normalized_entities: tuple[NormalizedEntity, ...],
    view_nodes: list[tuple[ViewRegion, DrawingNode]],
    entity_nodes: dict[str, DrawingNode],
    polygonal_cuts: list[DrawingNode],
) -> None:
    kinds = {
        SemanticLayer.BOLT_MARK: DrawingNodeKind.BOLT_MARK,
        SemanticLayer.PART_MARK: DrawingNodeKind.PART_MARK,
        SemanticLayer.SECTION: DrawingNodeKind.SECTION_SYMBOL,
    }
    grouped: dict[tuple[SemanticLayer, str], list[NormalizedEntity]] = {}
    for entity in normalized_entities:
        if entity.semantic_role in kinds:
            grouped.setdefault(
                (entity.semantic_role, entity.container_id),
                [],
            ).append(entity)
    physical_cuts = [
        entity
        for entity in normalized_entities
        if entity.semantic_role == SemanticLayer.PHYSICAL_CUT
        and entity.geometry is not None
        and entity.geometry.center is not None
        and entity.geometry.radius is not None
        and entity.source_id in entity_nodes
    ]
    explicit_containers = {
        container.container_id
        for container in source.containers
        if container.explicit_block
    }
    object_groups = []
    for (role, container_id), items in grouped.items():
        partitions = (
            [items]
            if container_id in explicit_containers
            else _ungrouped_annotation_objects(role, items)
        )
        object_groups.extend(
            (role, container_id, partition_index, partition)
            for partition_index, partition in enumerate(partitions)
        )
    for role, container_id, partition_index, items in sorted(
        object_groups,
        key=lambda item: (item[0].value, item[1], item[2]),
    ):
        items.sort(key=lambda item: item.source_id)
        texts = sorted(
            entity.normalized_text
            for entity in items
            if entity.normalized_text is not None
        )
        sources = tuple(entity.source_id for entity in items)
        bbox = _group_bbox(items)
        node = graph.add_node(
            kinds[role],
            source_ids=sources,
            attributes={
                "container_id": container_id,
                "object_partition": partition_index,
                "text": texts[0] if texts else None,
                "texts": texts,
                "bbox_mm": _bbox_payload(bbox),
                "entity_count": len(items),
            },
        )
        for entity in items:
            graph.add_edge(
                node,
                DrawingEdgeKind.CONTAINS,
                entity_nodes[entity.source_id],
                source_ids=(entity.source_id,),
                rule_id="TEKLA.ANNOTATION.CONTAINER_GROUP",
                residual_mm=0.0,
                strength=AssociationStrength.EXPLICIT,
            )

        line_endpoints = [
            point
            for entity in items
            if (points := _line_points(entity)) is not None
            for point in points
        ]
        if role == SemanticLayer.BOLT_MARK and line_endpoints:
            candidates = []
            for cut in physical_cuts:
                assert cut.geometry is not None
                assert cut.geometry.center is not None
                assert cut.geometry.radius is not None
                center = Point2D(*cut.geometry.center)
                distance, endpoint = min(
                    (
                        (point.distance_to(center), point)
                        for point in line_endpoints
                    ),
                    key=lambda item: (item[0], item[1].x, item[1].y),
                )
                if distance <= cut.geometry.radius + 1.0:
                    candidates.append(
                        (
                            distance,
                            cut.source_id,
                            endpoint,
                            entity_nodes[cut.source_id],
                            float(cut.geometry.radius),
                        )
                    )
            for cut_node in polygonal_cuts:
                center_value = cut_node.attributes.get("center_mm")
                diameter_value = cut_node.attributes.get("nominal_diameter_mm")
                if not isinstance(center_value, list) or len(center_value) != 2:
                    continue
                center = Point2D(float(center_value[0]), float(center_value[1]))
                radius = float(diameter_value or 0.0) / 2.0
                distance, endpoint = min(
                    (
                        (point.distance_to(center), point)
                        for point in line_endpoints
                    ),
                    key=lambda item: (item[0], item[1].x, item[1].y),
                )
                if distance <= radius + 1.0:
                    candidates.append(
                        (distance, cut_node.node_id, endpoint, cut_node, radius)
                    )
            if candidates:
                distance, _, endpoint, cut_node, radius = min(
                    candidates,
                    key=lambda item: (item[0], item[1]),
                )
                graph.add_edge(
                    node,
                    DrawingEdgeKind.LABELS,
                    cut_node,
                    source_ids=(*sources, *cut_node.source_ids),
                    rule_id="TEKLA.BOLT_MARK.LEADER_TO_CUT",
                    residual_mm=distance,
                    strength=AssociationStrength.GEOMETRIC,
                    attributes={
                        "leader_endpoint": [endpoint.x, endpoint.y],
                        "cut_radius_mm": radius,
                    },
                )
        elif role == SemanticLayer.PART_MARK:
            anchors = line_endpoints or [
                point
                for entity in items
                if (point := _position(entity)) is not None
            ]
            candidates = [
                (
                    min(_point_bbox_distance(point, region.bbox) for point in anchors),
                    region.region_id,
                    view_node,
                )
                for region, view_node in view_nodes
                if anchors
            ]
            if candidates:
                distance, _, view_node = min(candidates, key=lambda item: item[:2])
                if distance <= 1.0:
                    graph.add_edge(
                        node,
                        DrawingEdgeKind.LABELS,
                        view_node,
                        source_ids=sources,
                        rule_id="TEKLA.PART_MARK.LEADER_TO_VIEW",
                        residual_mm=distance,
                        strength=AssociationStrength.GEOMETRIC,
                    )
        elif role == SemanticLayer.SECTION:
            segments = [
                points
                for entity in items
                if (points := _line_points(entity)) is not None
            ]
            candidates = sorted(
                (
                    min(
                        _segment_bbox_distance(start, end, region.bbox)
                        for start, end in segments
                    ),
                    region.region_id,
                    view_node,
                )
                for region, view_node in view_nodes
                if segments
            )
            touching = [item for item in candidates if item[0] <= 1.0]
            if len(touching) == 1:
                distance, _, view_node = touching[0]
                graph.add_edge(
                    node,
                    DrawingEdgeKind.LABELS,
                    view_node,
                    source_ids=sources,
                    rule_id="TEKLA.SECTION.CUT_LINE_TO_VIEW",
                    residual_mm=distance,
                    strength=AssociationStrength.GEOMETRIC,
                    attributes={
                        "semantic_role": "section_line_view_support",
                        "profile_dimensions_inferred": False,
                    },
                )


def _parallel_angle(first: float, second: float) -> float:
    delta = abs((first - second) % pi)
    return min(delta, pi - delta)


def _add_metadata_rows(
    graph: DrawingGraph,
    normalized_entities: tuple[NormalizedEntity, ...],
    entity_nodes: dict[str, DrawingNode],
) -> None:
    texts = [
        entity
        for entity in normalized_entities
        if entity.normalized_text is not None and _position(entity) is not None
    ]
    for profile in texts:
        if not _PROFILE_RE.search(profile.normalized_text or ""):
            continue
        angle = (profile.text_rotation or 0.0) * pi / 180.0
        axis = Point2D(cos(angle), sin(angle))
        normal = Point2D(-axis.y, axis.x)
        anchor = _position(profile)
        assert anchor is not None
        tolerance = max(2.0, (profile.text_height or 1.0) * 0.35)
        row: list[tuple[float, NormalizedEntity]] = []
        for token in texts:
            position = _position(token)
            assert position is not None
            dx = position.x - anchor.x
            dy = position.y - anchor.y
            perpendicular = abs(dx * normal.x + dy * normal.y)
            token_angle = (token.text_rotation or 0.0) * pi / 180.0
            if perpendicular > tolerance or _parallel_angle(angle, token_angle) > 2.0 * pi / 180.0:
                continue
            along = dx * axis.x + dy * axis.y
            row.append((along, token))
        row.sort(key=lambda item: (item[0], item[1].normalized_text or ""))
        row_entities = [item[1] for item in row]
        part = min(
            (
                token
                for token in row_entities
                if _PART_RE.fullmatch(token.normalized_text or "")
                and not _MATERIAL_RE.fullmatch(token.normalized_text or "")
            ),
            key=lambda token: abs(next(value for value, item in row if item is token)),
            default=None,
        )
        numbers = [
            token
            for token in row_entities
            if token is not profile and _NUMBER_RE.fullmatch(token.normalized_text or "")
        ]
        length = min(
            numbers,
            key=lambda token: abs(next(value for value, item in row if item is token)),
            default=None,
        )
        material = next(
            (token for token in row_entities if _MATERIAL_RE.fullmatch(token.normalized_text or "")),
            None,
        )
        scale = next(
            (token for token in row_entities if _SCALE_RE.search(token.normalized_text or "")),
            None,
        )
        if length is None:
            continue
        semantic_tokens = {
            token.source_id: token
            for token in (part, profile, length, material, scale)
            if token is not None
        }
        row_entities = [
            token
            for _, token in row
            if token.source_id in semantic_tokens
        ]
        sources = tuple(token.source_id for token in row_entities)
        node = graph.add_node(
            DrawingNodeKind.METADATA_ROW,
            source_ids=sources,
            attributes={
                "part_number": part.normalized_text if part else None,
                "profile": profile.normalized_text,
                "nominal_length_mm": float(length.normalized_text or "nan"),
                "material": material.normalized_text if material else None,
                "scale": scale.normalized_text if scale else None,
                "tokens": [token.normalized_text for token in row_entities],
                "axis": [axis.x, axis.y],
                "completeness": sum(item is not None for item in (part, length, material, scale)),
            },
        )
        for token in row_entities:
            graph.add_edge(
                node,
                DrawingEdgeKind.HAS_TOKEN,
                entity_nodes[token.source_id],
                source_ids=(token.source_id,),
                rule_id="TEKLA.METADATA.ALIGNED_TEXT_ROW",
                residual_mm=0.0,
                strength=AssociationStrength.GEOMETRIC,
            )


def build_drawing_graph(
    source: SourceDocument,
    regions: RegionBuildResult,
    frame: LocalFrame,
    dialect: BHDialectProfile = DEFAULT_TEKLA_DIALECT,
) -> DrawingGraph:
    """Associate source facts in canonical member coordinates.

    ``frame`` is part of the public contract even though ``regions`` already
    contains normalized entities.  Callers must therefore make the coordinate
    choice explicit and cannot accidentally build a page-coordinate graph.
    """

    del frame
    graph = DrawingGraph()
    normalized = {entity.source_id: entity for entity in regions.normalized_entities}
    source_by_id = {entity.source_id: entity for entity in source.entities}
    entity_nodes: dict[str, DrawingNode] = {}
    for entity in sorted(regions.normalized_entities, key=lambda item: item.source_id):
        if not _is_association_fact(entity):
            continue
        original = source_by_id[entity.source_id]
        attributes = {
            "container_id": entity.container_id,
            "entity_type": entity.entity_type,
            "semantic_role": entity.semantic_role.value,
            "visibility": entity.visibility.value,
            "text": entity.normalized_text,
            "text_height": entity.text_height,
            "text_rotation": entity.text_rotation,
            "bbox_mm": _bbox_payload(entity.bbox),
        }
        if entity.entity_type == "DIMENSION":
            geometry = entity.geometry
            orientation = "unknown"
            if geometry is not None and len(geometry.coordinates) >= 3:
                first = Point2D(*geometry.coordinates[1])
                second = Point2D(*geometry.coordinates[2])
                orientation = (
                    "horizontal"
                    if abs(second.x - first.x) >= abs(second.y - first.y)
                    else "vertical"
                )
            attributes.update(
                {
                    "representation": "explicit",
                    "value_mm": original.dimension_measurement,
                    "orientation": orientation,
                }
            )
        entity_nodes[entity.source_id] = graph.add_node(
            _entity_kind(entity),
            source_ids=(entity.source_id,),
            attributes=attributes,
        )

    view_nodes = [
        (region, _view_node_for_region(graph, region))
        for region in regions.part_views
    ]
    for region, view_node in view_nodes:
        for source_id in region.source_ids:
            if source_id not in entity_nodes:
                continue
            graph.add_edge(
                view_node,
                DrawingEdgeKind.CONTAINS,
                entity_nodes[source_id],
                source_ids=(source_id,),
                rule_id="REGION.SOURCE_MEMBERSHIP",
                residual_mm=0.0,
                strength=(
                    AssociationStrength.EXPLICIT
                    if region.explicit_block
                    else AssociationStrength.GEOMETRIC
                ),
            )

    _add_physical_cut_projections(
        graph,
        regions.normalized_entities,
        view_nodes,
        entity_nodes,
    )
    polygonal_cuts = _add_polygonal_cut_groups(
        graph,
        regions.normalized_entities,
        view_nodes,
        entity_nodes,
    )
    _add_explicit_dimensions(graph, source, normalized, view_nodes, entity_nodes)
    _add_pattern_callout_dimensions(
        graph,
        source,
        regions.normalized_entities,
        view_nodes,
        dialect,
        entity_nodes,
    )
    _add_exploded_dimensions(
        graph,
        source,
        regions.normalized_entities,
        view_nodes,
        dialect,
    )
    _add_composite_annotations(
        graph,
        source,
        regions.normalized_entities,
        view_nodes,
        entity_nodes,
        polygonal_cuts,
    )
    _add_metadata_rows(graph, regions.normalized_entities, entity_nodes)
    return graph
