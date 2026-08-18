from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from math import hypot
import ezdxf
from ezdxf.entities import DXFEntity, Insert
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box
from shapely.ops import linemerge, nearest_points, unary_union

from .bh_bolt_semantics import opening_nominal_width, polygonize_closed_bolt_linework

from .bh_geometry import (
    PartBlock,
    ProjectionAnnotationMask,
    choose_long_axis,
    estimate_flange_developments,
    extend_flange_polygon_to_length,
    polygon_to_bulge_contours,
    select_flange_polygons,
    select_web_polygon,
    solid_part_entities,
    source_arcs,
)
from .bh_development import (
    assess_flange_development_semantics,
    quantize_derived_flange_length,
)
from .bh_knowledge import BHFlangeDevelopmentPolicy
from .bh_models import BHAssembly, BHMetadata, BHPlate, BHPlateRole, CircularCut, Point2D
from .bh_projection_semantics import analyse_projection_boundary, evaluate_boundary_repair
from .geometry_types import BoundingBox
from .bh_text import canonical_bh_label
from .bh_trace import TraceObserver, emit_trace
from .bh_trace_geometry import (
    contour_shape,
    cut_shapes,
    entity_shapes,
    polygon_shape,
    polygon_shapes,
)
from .dxf_io import normalize_text, recursive_virtual_entities


@dataclass(slots=True)
class BHBlockInstance:
    insert: Insert
    entities: list[DXFEntity]
    layer_counts: Counter[str]
    texts: list[str]
    entity_source_ids: tuple[str, ...] = ()

    @property
    def handle(self) -> str:
        return self.insert.dxf.handle or ""

    @property
    def name(self) -> str:
        return self.insert.dxf.name


@dataclass(frozen=True, slots=True)
class OwnedCircularCut:
    """A source-owned cut used only while lowering drawing facts.

    Manufacturing geometry remains ``CircularCut``.  This intermediate keeps
    source identity and selected-view ownership alive until the plate
    provenance has been emitted.
    """

    geometry: CircularCut
    source_ids: tuple[str, ...]
    source_blocks: tuple[str, ...]
    source_region_id: str | None

    @property
    def center(self) -> Point2D:
        return self.geometry.center

    @property
    def radius(self) -> float:
        return self.geometry.radius

    def translated(self, dx: float, dy: float) -> CircularCut:
        return self.geometry.translated(dx, dy)


@dataclass(frozen=True, slots=True)
class OwnedPolygonalOpening:
    """A closed Bolt-layer loop owned by one selected plate projection."""

    geometry: Polygon
    source_ids: tuple[str, ...]
    source_blocks: tuple[str, ...]
    source_region_id: str | None


@dataclass(frozen=True, slots=True)
class _BoltSymbolStroke:
    center: Point2D
    length: float
    direction_x: float
    direction_y: float


def _edge_view_symbol_centers(
    strokes: list[_BoltSymbolStroke],
) -> list[Point2D]:
    """Recognize Tekla's symmetric three-stroke edge-view bolt symbol."""

    result: list[Point2D] = []
    for triple in combinations(strokes, 3):
        reference = triple[0]
        if any(
            abs(
                reference.direction_x * item.direction_y
                - reference.direction_y * item.direction_x
            )
            > 0.05
            for item in triple[1:]
        ):
            continue
        normal_x = -reference.direction_y
        normal_y = reference.direction_x
        ordered = sorted(
            triple,
            key=lambda item: item.center.x * normal_x + item.center.y * normal_y,
        )
        left, middle, right = ordered
        left_position = left.center.x * normal_x + left.center.y * normal_y
        middle_position = middle.center.x * normal_x + middle.center.y * normal_y
        right_position = right.center.x * normal_x + right.center.y * normal_y
        first_gap = middle_position - left_position
        second_gap = right_position - middle_position
        outer_average = (left.length + right.length) / 2.0
        if first_gap <= 0.0 or second_gap <= 0.0:
            continue
        if abs(first_gap - second_gap) > max(1.0, 0.2 * max(first_gap, second_gap)):
            continue
        if abs(left.length - right.length) > max(1.0, 0.15 * outer_average):
            continue
        if middle.length < outer_average + max(1.0, 0.08 * outer_average):
            continue
        tangent_positions = [
            item.center.x * reference.direction_x
            + item.center.y * reference.direction_y
            for item in triple
        ]
        if max(tangent_positions) - min(tangent_positions) > 0.5 * middle.length:
            continue
        center = Point2D(
            sum(item.center.x for item in triple) / 3.0,
            sum(item.center.y for item in triple) / 3.0,
        )
        if any(center.distance_to(existing) <= 1.0 for existing in result):
            continue
        result.append(center)
    return result


def collect_bh_instances(doc: ezdxf.document.Drawing) -> list[BHBlockInstance]:
    result: list[BHBlockInstance] = []
    for insert in doc.modelspace().query("INSERT"):
        entities = list(recursive_virtual_entities(insert))
        result.append(
            BHBlockInstance(
                insert=insert,
                entities=entities,
                layer_counts=Counter(entity.dxf.layer for entity in entities),
                texts=[
                    normalize_text(entity.dxf.text)
                    for entity in entities
                    if entity.dxftype() == "TEXT" and normalize_text(entity.dxf.text)
                ],
                entity_source_ids=tuple(
                    (
                        f"{insert.dxf.handle or ''}:{insert.dxf.name}:"
                        f"{entity.dxf.handle or index}"
                    )
                    for index, entity in enumerate(entities)
                ),
            )
        )
    return result


def _deduplicate_cuts(
    cuts: list[OwnedCircularCut],
    tolerance: float = 0.01,
) -> list[OwnedCircularCut]:
    result: list[OwnedCircularCut] = []
    for cut in cuts:
        match_index = next(
            (
                index
                for index, existing in enumerate(result)
                if cut.center.distance_to(existing.center) <= tolerance
                and abs(cut.radius - existing.radius) <= tolerance
            ),
            None,
        )
        if match_index is not None:
            existing = result[match_index]
            result[match_index] = OwnedCircularCut(
                geometry=existing.geometry,
                source_ids=tuple(sorted(set((*existing.source_ids, *cut.source_ids)))),
                source_blocks=tuple(
                    sorted(set((*existing.source_blocks, *cut.source_blocks)))
                ),
                source_region_id=existing.source_region_id,
            )
            continue
        result.append(cut)
    return result


def _polygonize_bolt_line_openings(entities: list[DXFEntity]) -> list[Polygon]:
    """Recover independently closed physical openings drawn as Bolt lines.

    Tekla may export a non-circular hole as a closed chain of LINE entities
    plus dangling center marks.  ``polygonize`` admits only complete rings, so
    center marks, edge-view symbols and annotation leaders cannot become cuts.
    Processing the raw segments (without noding their crossings) also keeps a
    center cross from splitting one opening into several false openings.
    """

    linework = [
        LineString(
            (
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            )
        )
        for entity in entities
        if entity.dxftype() == "LINE" and entity.dxf.layer == "Bolt"
    ]
    return polygonize_closed_bolt_linework(linework)


def _collect_bolt_line_openings(
    instances: list[BHBlockInstance],
    view: PartBlock,
    selected_plate: Polygon,
    *,
    tolerance: float = 0.05,
) -> tuple[list[OwnedPolygonalOpening], list[str]]:
    """Collect complete Bolt-line loops that are geometrically owned by a plate."""

    owned: list[OwnedPolygonalOpening] = []
    view_bounds = view.bbox.expanded(tolerance)
    plate_region = selected_plate.buffer(tolerance)
    for instance in instances:
        polygons = _polygonize_bolt_line_openings(instance.entities)
        if not polygons:
            continue
        line_rows = [
            (index, entity, LineString(
                (
                    (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                    (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                )
            ))
            for index, entity in enumerate(instance.entities)
            if entity.dxftype() == "LINE" and entity.dxf.layer == "Bolt"
        ]
        for polygon in polygons:
            min_x, min_y, max_x, max_y = polygon.bounds
            if not (
                view_bounds.min_x <= min_x
                and view_bounds.min_y <= min_y
                and max_x <= view_bounds.max_x
                and max_y <= view_bounds.max_y
                and plate_region.covers(polygon)
            ):
                continue
            boundary_band = polygon.boundary.buffer(tolerance)
            source_ids = tuple(
                sorted(
                    {
                        instance.entity_source_ids[index]
                        for index, _, line in line_rows
                        if index < len(instance.entity_source_ids)
                        and boundary_band.covers(line)
                    }
                )
            )
            candidate = OwnedPolygonalOpening(
                geometry=polygon,
                source_ids=source_ids,
                source_blocks=(instance.name,),
                source_region_id=view.region_id,
            )
            duplicate_index = next(
                (
                    index
                    for index, existing in enumerate(owned)
                    if polygon.hausdorff_distance(existing.geometry) <= tolerance
                ),
                None,
            )
            if duplicate_index is None:
                owned.append(candidate)
                continue
            existing = owned[duplicate_index]
            owned[duplicate_index] = OwnedPolygonalOpening(
                geometry=existing.geometry,
                source_ids=tuple(sorted(set((*existing.source_ids, *source_ids)))),
                source_blocks=tuple(
                    sorted(set((*existing.source_blocks, instance.name)))
                ),
                source_region_id=existing.source_region_id,
            )
    return owned, sorted(
        {block for opening in owned for block in opening.source_blocks}
    )


def _match_polygon_interiors_to_openings(
    polygon: Polygon,
    openings: list[OwnedPolygonalOpening],
    *,
    tolerance: float = 0.05,
) -> list[OwnedPolygonalOpening | None]:
    """Align source opening evidence to Shapely's actual interior-ring order."""

    matches: list[OwnedPolygonalOpening | None] = []
    used: set[int] = set()
    for ring in polygon.interiors:
        interior = Polygon(ring.coords)
        candidates = [
            (interior.hausdorff_distance(opening.geometry), index, opening)
            for index, opening in enumerate(openings)
            if index not in used
        ]
        match = min(candidates, default=None)
        if match is None or match[0] > tolerance:
            matches.append(None)
            continue
        used.add(match[1])
        matches.append(match[2])
    return matches


def _subtract_owned_openings(
    polygon: Polygon,
    openings: list[OwnedPolygonalOpening],
) -> Polygon:
    if not openings:
        return polygon
    opened = polygon.difference(
        unary_union([opening.geometry for opening in openings])
    )
    if not isinstance(opened, Polygon):
        raise ValueError("Bolt-line openings split a plate into multiple bodies.")
    matched = _match_polygon_interiors_to_openings(opened, openings)
    if sum(item is not None for item in matched) != len(openings):
        raise ValueError("Closed Bolt-line openings did not produce stable inner contours.")
    return opened


def _filter_projection_cell_interiors(
    polygon: Polygon,
    entities: list[DXFEntity],
    *,
    tolerance_mm: float,
) -> tuple[Polygon, dict[str, object]]:
    """Drop Part-line cells crossed by a source-backed hidden projection chord."""

    hidden_lines = [
        LineString(
            (
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            )
        )
        for entity in entities
        if entity.dxftype() == "LINE"
        and entity.dxf.layer == "Part"
        and entity.dxf.linetype == "XKITLINE04"
    ]
    hidden_union = unary_union(hidden_lines) if hidden_lines else MultiLineString([])
    if isinstance(hidden_union, MultiLineString):
        hidden_union = linemerge(hidden_union)

    def line_components(geometry: object) -> list[LineString]:
        if isinstance(geometry, LineString):
            return [geometry]
        return [
            component
            for item in getattr(geometry, "geoms", ())
            for component in line_components(item)
        ]

    hidden_components = line_components(hidden_union)

    kept_rings: list[tuple[tuple[float, float], ...]] = []
    rejected_bounds: list[list[float]] = []
    chord_count = 0
    for ring in polygon.interiors:
        interior = Polygon(ring.coords)
        projection_chords = 0
        continuation_contacts: list[Point] = []
        for line in hidden_components:
            inside = line.intersection(interior)
            segments = (
                [inside]
                if isinstance(inside, LineString)
                else [
                    item
                    for item in getattr(inside, "geoms", ())
                    if isinstance(item, LineString)
                ]
            )
            # A Tekla hidden projection course can be emitted as several
            # end-to-end LINE entities.  Judge the merged source course, not an
            # arbitrary DXF segment boundary, while still requiring two true
            # contacts with the visible cell boundary.
            if any(
                segment.length > tolerance_mm
                and Point(segment.coords[0]).distance(interior.boundary)
                <= tolerance_mm
                and Point(segment.coords[-1]).distance(interior.boundary)
                <= tolerance_mm
                and Point(segment.coords[0]).distance(Point(segment.coords[-1]))
                > tolerance_mm
                for segment in segments
            ):
                projection_chords += 1
            if (
                line.distance(interior.boundary) <= tolerance_mm
                and line.difference(interior.buffer(tolerance_mm)).length
                > tolerance_mm
            ):
                contact = nearest_points(line, interior.boundary)[1]
                if all(
                    contact.distance(existing) > tolerance_mm
                    for existing in continuation_contacts
                ):
                    continuation_contacts.append(contact)
        if len(continuation_contacts) >= 2:
            projection_chords += len(continuation_contacts)
        if projection_chords:
            rejected_bounds.append([float(value) for value in interior.bounds])
            chord_count += projection_chords
            continue
        kept_rings.append(tuple((float(x), float(y)) for x, y in ring.coords))

    if len(kept_rings) == len(polygon.interiors):
        return polygon, {
            "rejected_count": 0,
            "kept_count": len(kept_rings),
            "hidden_projection_chord_count": 0,
            "rejected_bounds": [],
        }

    filtered = Polygon(polygon.exterior.coords, kept_rings)
    if not filtered.is_valid or filtered.area <= 0:
        raise ValueError("Filtering projection-cell interiors produced an invalid web polygon.")
    if filtered.exterior.hausdorff_distance(polygon.exterior) > 1e-9:
        raise ValueError("Filtering projection-cell interiors changed the web outer boundary.")
    return filtered, {
        "rejected_count": len(rejected_bounds),
        "kept_count": len(kept_rings),
        "hidden_projection_chord_count": chord_count,
        "rejected_bounds": rejected_bounds,
    }


def _collect_circular_cuts(
    instances: list[BHBlockInstance],
    view: PartBlock,
    *,
    margin: float = 120.0,
) -> tuple[list[OwnedCircularCut], list[str]]:
    """Collect only physical Bolt/CIRCLE cuts in one drawing view.

    Bolt/LINE center marks are intentionally ignored and can never reach the
    output document.  An empty result is valid for a hole-less plate.
    """
    bbox = view.bbox.expanded(margin)
    cuts: list[OwnedCircularCut] = []
    for instance in instances:
        for index, entity in enumerate(instance.entities):
            if entity.dxf.layer != "Bolt" or entity.dxftype() != "CIRCLE":
                continue
            center = Point2D(float(entity.dxf.center.x), float(entity.dxf.center.y))
            if not (bbox.min_x <= center.x <= bbox.max_x and bbox.min_y <= center.y <= bbox.max_y):
                continue
            source_id = (
                instance.entity_source_ids[index]
                if index < len(instance.entity_source_ids)
                else f"{instance.handle}:{instance.name}:{entity.dxf.handle or index}"
            )
            cuts.append(
                OwnedCircularCut(
                    geometry=CircularCut(center, float(entity.dxf.radius)),
                    source_ids=(source_id,),
                    source_blocks=(instance.name,),
                    source_region_id=view.region_id,
                )
            )
    owned = _deduplicate_cuts(cuts)
    source_blocks = sorted(
        {block for cut in owned for block in cut.source_blocks}
    )
    return owned, source_blocks


def _projection_annotation_masks(
    instances: list[BHBlockInstance],
) -> tuple[ProjectionAnnotationMask, ...]:
    """Preserve Tekla mark groups as possible projection-mask evidence.

    Only canonical PartMark/BoltMark entities are admitted.  Geometry decides
    whether one complete group actually covers a collinear silhouette gap;
    unrelated text and drawing tables never become repair authority.
    """

    masks: list[ProjectionAnnotationMask] = []
    for instance in instances:
        for semantic_layer in ("BoltMark", "PartMark"):
            selected = [
                (index, entity)
                for index, entity in enumerate(instance.entities)
                if str(getattr(entity.dxf, "layer", "")) == semantic_layer
                and entity.dxftype() in {"TEXT", "MTEXT", "LINE"}
            ]
            if not selected:
                continue
            masks.append(
                ProjectionAnnotationMask(
                    semantic_layer=semantic_layer,
                    entities=tuple(entity for _, entity in selected),
                    source_ids=tuple(
                        (
                            instance.entity_source_ids[index]
                            if index < len(instance.entity_source_ids)
                            else (
                                f"{instance.handle}:{instance.name}:"
                                f"{entity.dxf.handle or index}"
                            )
                        )
                        for index, entity in selected
                    ),
                )
            )
    return tuple(masks)


def _axis_measure(bounds: tuple[float, float, float, float], long_axis: str) -> tuple[float, float]:
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    return (width, height) if long_axis == "x" else (height, width)


def _transverse_center(bounds: tuple[float, float, float, float], long_axis: str) -> float:
    return (bounds[1] + bounds[3]) / 2.0 if long_axis == "x" else (bounds[0] + bounds[2]) / 2.0


def _main_flange_side_spans(
    faces: list[Polygon],
    web_polygon: Polygon,
    *,
    long_axis: str,
    flange_thickness: float,
    nominal_length: float,
) -> dict[str, float]:
    web_center = _transverse_center(web_polygon.bounds, long_axis)
    spans: dict[str, float] = {}
    for face in faces:
        length, transverse = _axis_measure(face.bounds, long_axis)
        if not (0.35 * flange_thickness <= transverse <= 1.80 * flange_thickness):
            continue
        if length < max(0.10 * nominal_length, 100.0):
            continue
        adjacency_tolerance = max(0.15, 0.02 * flange_thickness)
        adjacent_boundary_length = face.boundary.intersection(
            web_polygon.boundary.buffer(
                adjacency_tolerance,
                cap_style=2,
                join_style=2,
            )
        ).length
        if adjacent_boundary_length < 0.80 * length:
            continue
        side = "low" if _transverse_center(face.bounds, long_axis) < web_center else "high"
        spans[side] = max(spans.get(side, 0.0), length)
    return spans


def _source_backed_main_flange_side_spans(
    entities: list[DXFEntity],
    web_polygon: Polygon,
    *,
    long_axis: str,
    flange_thickness: float,
    nominal_length: float,
    manufacturing_tolerance_mm: float,
    outer_endpoint_envelope: bool = False,
) -> dict[str, float]:
    """Read each flange's longer edge from a complete source-course pair.

    A longitudinal BH view exposes two courses on each side of the clear web:
    the plate edge and its bevel-side course.  Polygonisation can merge either
    thin strip into the web depending on the precision grid, so these four
    direct source lines are the stable authority.  A side is admitted only
    when both courses exist one flange thickness apart and substantially
    overlap; isolated internal or auxiliary lines cannot create a flange.
    For a proved parallel bevel pair, ``outer_endpoint_envelope`` measures
    between the outside end points of those same two courses.  It never uses
    the longer overall projection/member span as the flange length.
    """

    if long_axis not in {"x", "y"} or flange_thickness <= 0.0:
        return {}
    bounds = web_polygon.bounds
    web_low = bounds[1] if long_axis == "x" else bounds[0]
    web_high = bounds[3] if long_axis == "x" else bounds[2]
    expected_courses = {
        "low": (web_low - flange_thickness, web_low),
        "high": (web_high, web_high + flange_thickness),
    }
    position_tolerance = max(
        manufacturing_tolerance_mm,
        0.02 * flange_thickness,
    )
    minimum_span = max(100.0, 0.10 * nominal_length)
    intervals: dict[str, list[list[tuple[float, float]]]] = {
        side: [[], []] for side in expected_courses
    }

    for entity in solid_part_entities(entities):
        if entity.dxftype() != "LINE":
            continue
        start = entity.dxf.start
        end = entity.dxf.end
        start_long = float(start.x if long_axis == "x" else start.y)
        end_long = float(end.x if long_axis == "x" else end.y)
        start_transverse = float(start.y if long_axis == "x" else start.x)
        end_transverse = float(end.y if long_axis == "x" else end.x)
        longitudinal_span = abs(end_long - start_long)
        if (
            longitudinal_span < minimum_span
            or abs(end_transverse - start_transverse) > position_tolerance
        ):
            continue
        transverse = 0.5 * (start_transverse + end_transverse)
        matches = [
            (side, index)
            for side, positions in expected_courses.items()
            for index, position in enumerate(positions)
            if abs(transverse - position) <= position_tolerance
        ]
        if len(matches) != 1:
            continue
        side, index = matches[0]
        intervals[side][index].append(
            (min(start_long, end_long), max(start_long, end_long))
        )

    def longest_continuous_interval(
        rows: list[tuple[float, float]],
    ) -> tuple[float, float] | None:
        if not rows:
            return None
        merged: list[list[float]] = []
        for low, high in sorted(rows):
            if merged and low <= merged[-1][1] + position_tolerance:
                merged[-1][1] = max(merged[-1][1], high)
            else:
                merged.append([low, high])
        low, high = max(merged, key=lambda row: row[1] - row[0])
        return low, high

    spans: dict[str, float] = {}
    for side, course_rows in intervals.items():
        first = longest_continuous_interval(course_rows[0])
        second = longest_continuous_interval(course_rows[1])
        if first is None or second is None:
            continue
        first_length = first[1] - first[0]
        second_length = second[1] - second[0]
        overlap = min(first[1], second[1]) - max(first[0], second[0])
        if overlap < 0.80 * min(first_length, second_length):
            continue
        if outer_endpoint_envelope:
            start_shift = second[0] - first[0]
            end_shift = second[1] - first[1]
            if abs(start_shift - end_shift) > position_tolerance:
                continue
            spans[side] = max(first[1], second[1]) - min(first[0], second[0])
        else:
            spans[side] = max(first_length, second_length)
    return spans


def _validated_direct_projection_rectangle(
    *,
    projected: Polygon,
    entities: list[DXFEntity],
    entity_source_ids: tuple[str, ...],
    projection_grid_mm: float,
    flange_axis: str,
    flange_width: float,
    main_flange_spans: dict[str, float],
    manufacturing_tolerance_mm: float,
) -> Polygon | None:
    bounds = projected.bounds
    projected_length = (
        bounds[2] - bounds[0]
        if flange_axis == "x"
        else bounds[3] - bounds[1]
    )
    projected_width = (
        bounds[3] - bounds[1]
        if flange_axis == "x"
        else bounds[2] - bounds[0]
    )
    bounding_area = projected_length * projected_width
    geometry_supported = (
        not projected.interiors
        and bounding_area > 0.0
        and projected.area / bounding_area >= 0.985
        and abs(projected_width - flange_width) <= manufacturing_tolerance_mm
        and any(
            abs(span - projected_length) <= manufacturing_tolerance_mm
            for span in main_flange_spans.values()
        )
    )
    if not geometry_supported:
        return None
    if flange_axis == "x":
        candidate = box(bounds[0], bounds[1], bounds[2], bounds[1] + flange_width)
    else:
        candidate = box(bounds[0], bounds[1], bounds[0] + flange_width, bounds[3])
    boundary_tolerance = max(1e-7, projection_grid_mm * 0.51)
    semantics = analyse_projection_boundary(
        projected,
        entities,
        entity_source_ids=entity_source_ids,
        association_tolerance_mm=boundary_tolerance,
    )
    if not semantics.direct_edges:
        return None
    decision = evaluate_boundary_repair(
        projected,
        candidate,
        semantics,
        fidelity_tolerance_mm=boundary_tolerance,
        repair_kind="direct_flange_projection_rectangularization",
    )
    return candidate if decision.applied and not decision.lost_source_ids else None


def _compact_bolt_line_side_counts(
    instances: list[BHBlockInstance],
    main: PartBlock,
    web_polygon: Polygon,
    *,
    long_axis: str,
    nominal_length: float,
) -> dict[str, int]:
    """Read edge-view hole symbols as semantics, never as output geometry."""
    counts = {"low": 0, "high": 0}
    web_center = _transverse_center(web_polygon.bounds, long_axis)
    bbox = main.bbox.expanded(30.0)
    physical_circles = [
        entity
        for instance in instances
        for entity in instance.entities
        if entity.dxf.layer == "Bolt" and entity.dxftype() == "CIRCLE"
    ]
    for instance in instances:
        closed_openings = _polygonize_bolt_line_openings(instance.entities)
        strokes: list[_BoltSymbolStroke] = []
        for line in instance.entities:
            if line.dxf.layer != "Bolt" or line.dxftype() != "LINE":
                continue
            start = Point2D(float(line.dxf.start.x), float(line.dxf.start.y))
            end = Point2D(float(line.dxf.end.x), float(line.dxf.end.y))
            center = Point2D((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)
            if any(
                center.distance_to(
                    Point2D(float(circle.dxf.center.x), float(circle.dxf.center.y))
                )
                <= max(float(circle.dxf.radius), 0.1)
                for circle in physical_circles
            ):
                continue
            if any(
                polygon.buffer(0.1).covers(Point(center.x, center.y))
                for polygon in closed_openings
            ):
                continue
            if not (
                bbox.min_x <= center.x <= bbox.max_x
                and bbox.min_y <= center.y <= bbox.max_y
            ):
                continue
            symbol_bbox = BoundingBox.from_points([start, end])
            length, transverse = _axis_measure(
                (
                    symbol_bbox.min_x,
                    symbol_bbox.min_y,
                    symbol_bbox.max_x,
                    symbol_bbox.max_y,
                ),
                long_axis,
            )
            if length > max(0.08 * nominal_length, 250.0) or transverse > 250.0:
                continue
            dx = end.x - start.x
            dy = end.y - start.y
            stroke_length = hypot(dx, dy)
            if stroke_length <= 1e-9:
                continue
            strokes.append(
                _BoltSymbolStroke(
                    center,
                    stroke_length,
                    dx / stroke_length,
                    dy / stroke_length,
                )
            )
        # Recognition uses the symbol's own symmetry and stroke lengths, not a
        # fixed page/model-space neighbourhood radius.
        for symbol_center in _edge_view_symbol_centers(strokes):
            side_value = (
                symbol_center.y if long_axis == "x" else symbol_center.x
            )
            counts["low" if side_value < web_center else "high"] += 1
    return counts


def _split_developed_flange_cuts(
    cuts: list[OwnedCircularCut],
    target_lengths: tuple[float, float],
    *,
    long_axis: str,
    src_bounds: tuple[float, float, float, float],
    side_counts: dict[str, int],
    tolerance_mm: float = 0.5,
) -> tuple[list[OwnedCircularCut], list[OwnedCircularCut]]:
    """Assign source-projection circles to the two rectangular flange plates.

    ``target_lengths`` follow the development order: index 0 is the upper
    flange (positive web side) and index 1 the lower flange.  A circle whose
    source longitudinal position exceeds the shorter plate's end belongs to
    the longer plate; a circle inside the overlap uses the edge-view Bolt side
    counts to pick the upper (high) vs lower (low) plate, so a flanged member
    with distinct upper/lower lengths and real flange holes is still split
    instead of silently collapsing to a same-length pair.
    """
    upper_len, lower_len = target_lengths
    longer_index = 0 if upper_len >= lower_len else 1
    short_len = min(upper_len, lower_len)
    if long_axis == "x":
        src_origin = src_bounds[0]
    else:
        src_origin = src_bounds[1]
    high_count = int(side_counts.get("high", 0) or 0)
    low_count = int(side_counts.get("low", 0) or 0)
    # High/low symbol counts are rare on the flange projection itself; when
    # absent, overlap circles go to the longer plate (the plate that extends
    # further is the one whose footprint covers the shared region).
    assignments: list[list[OwnedCircularCut]] = [[], []]
    for cut in cuts:
        position = (
            (cut.center.x if long_axis == "x" else cut.center.y) - src_origin
        )
        # Circles stay in the source-projection frame: the rectangular plates
        # are also emitted in that frame, so ``_normalize_polygon_plate``
        # applies one shared translation to outline and circles and cut
        # provenance (source_ids / blocks / region) stays intact.
        if position > short_len + tolerance_mm:
            assignments[longer_index].append(cut)
        elif high_count > low_count:
            assignments[0].append(cut)
        elif low_count > high_count:
            assignments[1].append(cut)
        else:
            assignments[longer_index].append(cut)
    return assignments[0], assignments[1]


def _assign_flange_cuts(
    polygons: list[Polygon],
    cuts: list[OwnedCircularCut],
    *,
    instances: list[BHBlockInstance],
    main: PartBlock,
    web_polygon: Polygon,
    all_main_faces: list[Polygon],
    nominal_length: float,
    flange_thickness: float,
    main_long_axis: str,
    flange_long_axis: str,
    main_flange_spans: dict[str, float] | None = None,
    polygonal_opening_count: int = 0,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> tuple[list[list[OwnedCircularCut]], bool, dict[str, object]]:
    assignments: list[list[OwnedCircularCut]] = [[] for _ in polygons]
    ambiguous: list[OwnedCircularCut] = []
    for cut in cuts:
        point = Point(cut.center.x, cut.center.y)
        covered = [
            index
            for index, polygon in enumerate(polygons)
            if polygon.buffer(0.05).covers(point)
        ]
        if len(covered) == 1:
            assignments[covered[0]].append(cut)
        elif covered:
            ambiguous.append(cut)
        else:
            # A source circle outside every selected plate is an extraction error.
            raise ValueError(
                f"Flange cut at ({cut.center.x:.3f}, {cut.center.y:.3f}) is outside all flange candidates."
            )

    spans = (
        dict(main_flange_spans)
        if main_flange_spans is not None
        else _main_flange_side_spans(
            all_main_faces,
            web_polygon,
            long_axis=main_long_axis,
            flange_thickness=flange_thickness,
            nominal_length=nominal_length,
        )
    )
    side_counts = _compact_bolt_line_side_counts(
        instances,
        main,
        web_polygon,
        long_axis=main_long_axis,
        nominal_length=nominal_length,
    )
    target_index: int | None = None
    active_sides = [side for side, count in side_counts.items() if count > 0 and side in spans]
    if ambiguous and len(active_sides) == 1:
        target_span = spans[active_sides[0]]
        target_index = min(
            range(len(polygons)),
            key=lambda index: abs(_axis_measure(polygons[index].bounds, flange_long_axis)[0] - target_span),
        )
        assignments[target_index].extend(ambiguous)
        ambiguous = []
    elif ambiguous and len(polygons) == 1:
        assignments[0].extend(ambiguous)
        ambiguous = []
    elif ambiguous:
        raise ValueError(
            "Flange circles overlap multiple flange projections and no unique edge-view Bolt symbol resolves the side."
        )

    # If only one outer geometry exists but line-symbol semantics prove holes are
    # on one physical side only, materialize two plates instead of quantity=2.
    split_single = bool(
        (cuts or polygonal_opening_count)
        and len(polygons) == 1
        and len(active_sides) == 1
    )
    diagnostics = {
        "flange_circle_count": len(cuts),
        "flange_polygonal_opening_count": polygonal_opening_count,
        "main_flange_side_spans_mm": spans,
        "main_bolt_line_symbol_counts": side_counts,
        "ambiguous_resolved_target_index": target_index,
        "split_single_geometry_for_asymmetric_cuts": split_single,
        "assigned_counts": [len(items) for items in assignments],
    }
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="flange_cut_ownership",
        status="observed" if cuts else "not_applicable",
        title_zh="翼缘圆孔归属",
        summary_zh=(
            f"将 {len(cuts)} 个圆孔分配给 {len(polygons)} 个翼缘轮廓"
            if cuts
            else "翼缘投影中无物理圆孔"
        ),
        hypothesis_id=hypothesis_id,
        shapes=(
            *polygon_shapes("flange-cut-owner", "face_selected", polygons),
            *cut_shapes("flange-owned-cut", "physical_cut", cuts),
        ),
        payload=diagnostics,
    )
    return assignments, split_single, diagnostics


def _retained_handles(instances: list[BHBlockInstance], profile_text: str) -> list[str]:
    frame = [instance for instance in instances if instance.layer_counts["DrawingSheet"] > 0]
    table = [instance for instance in instances if any(profile_text in text.upper() for text in instance.texts)]
    title = [
        instance
        for instance in instances
        if instance.layer_counts["OtherObjectType"] > 100
        and not any(profile_text in text.upper() for text in instance.texts)
    ]
    selected: list[BHBlockInstance] = []
    if frame:
        selected.append(max(frame, key=lambda item: item.layer_counts["DrawingSheet"]))
    if table:
        selected.append(max(table, key=lambda item: len(item.texts)))
    if title:
        selected.append(max(title, key=lambda item: len(item.entities)))
    handles = {instance.handle for instance in selected}
    return [instance.handle for instance in instances if instance.handle in handles]



def _entity_trace(entities: list[DXFEntity]) -> dict[str, object]:
    handles = [str(entity.dxf.handle) for entity in entities if getattr(entity.dxf, "handle", None)]
    return {
        "source_entity_count": len(entities),
        "source_entity_handles": handles,
        "source_entity_types": dict(Counter(entity.dxftype() for entity in entities)),
    }

def _normalize_polygon_plate(
    polygon: Polygon,
    *,
    entities: list[DXFEntity],
    role: BHPlateRole,
    thickness: float,
    label: str,
    quantity: int,
    cuts: list[CircularCut | OwnedCircularCut] | None = None,
    owned_openings: list[OwnedPolygonalOpening] | None = None,
    source_index: int = 0,
    grid_size: float,
    provenance: dict[str, object] | None = None,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> BHPlate:
    arcs = source_arcs(entities)
    outer, inner = polygon_to_bulge_contours(polygon, arcs, grid_size=grid_size)
    recovered_arcs = sum(abs(vertex.bulge) > 1e-12 for vertex in outer.vertices)
    recovered_arcs += sum(
        abs(vertex.bulge) > 1e-12
        for contour in inner
        for vertex in contour.vertices
    )
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="arc_chain_recovery",
        status="observed" if recovered_arcs else "not_applicable",
        title_zh="圆弧链恢复",
        summary_zh=(
            f"从源 ARC 证据恢复 {recovered_arcs} 段制造圆弧"
            if recovered_arcs
            else "选定边界未形成可验证的完整圆弧链"
        ),
        hypothesis_id=hypothesis_id,
        shapes=(
            polygon_shape(f"{role.value}-polygon-before-arc", "face_selected", polygon),
            contour_shape(f"{role.value}-contour-after-arc", "manufacturing_plate", outer),
        ),
        payload={
            "plate_role": role.value,
            "source_arc_count": len(arcs),
            "recovered_arc_count": recovered_arcs,
            "grid_size_mm": grid_size,
        },
    )
    inner_polygons = [Polygon(ring.coords) for ring in polygon.interiors]
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="inner_openings",
        status="observed" if inner_polygons else "not_applicable",
        title_zh="板内异形开口",
        summary_zh=(f"保留 {len(inner_polygons)} 个闭合异形开口" if inner_polygons else "该板无异形开口"),
        hypothesis_id=hypothesis_id,
        shapes=polygon_shapes(f"{role.value}-opening", "manufacturing_cut", inner_polygons),
        payload={"plate_role": role.value, "opening_count": len(inner_polygons)},
    )
    bbox = outer.bbox
    dx = -bbox.min_x
    dy = -bbox.min_y
    owned_cuts = list(cuts or [])
    source_openings = list(owned_openings or [])
    matched_openings = _match_polygon_interiors_to_openings(
        polygon,
        source_openings,
    )
    plate_provenance = dict(provenance or {})
    plate_provenance.update(
        {
            "normalization_translation_mm": [dx, dy],
            "circular_cut_source_ids": [
                list(cut.source_ids) if isinstance(cut, OwnedCircularCut) else []
                for cut in owned_cuts
            ],
            "circular_cut_source_blocks": [
                list(cut.source_blocks) if isinstance(cut, OwnedCircularCut) else []
                for cut in owned_cuts
            ],
            "inner_contour_source_ids": [
                list(opening.source_ids) if opening is not None else []
                for opening in matched_openings
            ],
            "inner_contour_source_blocks": [
                list(opening.source_blocks) if opening is not None else []
                for opening in matched_openings
            ],
            "inner_contour_nominal_diameters_mm": [
                opening_nominal_width(opening.geometry)
                if opening is not None
                else None
                for opening in matched_openings
            ],
            "polygonal_cut_count": len(source_openings),
        }
    )
    return BHPlate(
        role=role,
        contour=outer.translated(dx, dy),
        thickness=thickness,
        label=label,
        quantity=quantity,
        circular_cuts=[cut.translated(dx, dy) for cut in owned_cuts],
        inner_contours=[contour.translated(dx, dy) for contour in inner],
        source_index=source_index,
        area_mm2=float(polygon.area),
        provenance=plate_provenance,
    )


def lower_bh_assembly(
    *,
    metadata: BHMetadata,
    instances: list[BHBlockInstance],
    main: PartBlock,
    flange: PartBlock,
    manufacturing_tolerance_mm: float = 0.15,
    flange_development_policy: BHFlangeDevelopmentPolicy,
    development_profile_id: str,
    compiler_diagnostics: dict[str, object] | None = None,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> BHAssembly:
    """Lower resolved BH semantics into normalized plate geometry.

    This function is deliberately separated from DXF reading, metadata parsing
    and view selection.  It is the compiler back-end: the caller supplies a
    resolved semantic context and receives a manufacturing assembly.
    """
    def source_view_trace(block: PartBlock) -> dict[str, object]:
        source_view = block.source_view
        if source_view is None:
            return {}
        return {
            "source_region_id": source_view.region_id,
            "source_geometry_signature": source_view.geometry_signature,
            "source_entity_ids": list(source_view.source_ids),
            "source_container_ids": list(source_view.container_ids),
        }

    web_cuts, web_hole_blocks = _collect_circular_cuts(instances, main)
    flange_cuts, flange_hole_blocks = _collect_circular_cuts(instances, flange)
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="source_views_and_cuts",
        status="observed",
        title_zh="候选源视图与物理切口",
        summary_zh=(
            f"腹板视图 {main.name}、翼缘视图 {flange.name}；"
            f"物理圆孔 {len(web_cuts) + len(flange_cuts)} 个"
        ),
        hypothesis_id=hypothesis_id,
        shapes=(
            *entity_shapes("web-source", main.entities),
            *entity_shapes("flange-source", flange.entities),
            *cut_shapes("web-cut", "physical_cut", web_cuts),
            *cut_shapes("flange-cut", "physical_cut", flange_cuts),
        ),
        payload={
            "web_view": {"name": main.name, "handle": main.handle},
            "flange_view": {"name": flange.name, "handle": flange.handle},
            "web_cut_count": len(web_cuts),
            "flange_cut_count": len(flange_cuts),
            "web_hole_blocks": web_hole_blocks,
            "flange_hole_blocks": flange_hole_blocks,
        },
    )

    web_result = select_web_polygon(
        main.entities,
        entity_source_ids=main.entity_source_ids,
        profile_height=metadata.profile.max_height,
        nominal_length=metadata.nominal_length,
        hole_centers=[cut.center for cut in web_cuts],
        source_bbox=main.bbox,
        clear_web_height=(
            metadata.profile.clear_web_height
            if not metadata.profile.is_variable_height
            else None
        ),
        web_thickness=metadata.profile.web_thickness,
        observer=observer,
        hypothesis_id=hypothesis_id,
    )
    web_source_polygon, projection_cell_diagnostics = (
        _filter_projection_cell_interiors(
            web_result.polygon,
            main.entities,
            tolerance_mm=0.15,
        )
    )
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="web_projection_cell_interiors",
        status=(
            "repaired"
            if projection_cell_diagnostics["rejected_count"]
            else "not_applicable"
        ),
        title_zh="腹板投影单元内轮廓",
        summary_zh=(
            f"排除 {projection_cell_diagnostics['rejected_count']} 个隐藏线分割投影单元"
            if projection_cell_diagnostics["rejected_count"]
            else "腹板内轮廓未发现隐藏投影弦"
        ),
        hypothesis_id=hypothesis_id,
        shapes=polygon_shapes(
            "web-projection-cell",
            "repair_removed",
            [Polygon(ring.coords) for ring in web_result.polygon.interiors],
        ),
        payload=projection_cell_diagnostics,
    )
    web_openings, web_opening_blocks = _collect_bolt_line_openings(
        instances,
        main,
        web_source_polygon,
    )
    web_manufacturing_polygon = _subtract_owned_openings(
        web_source_polygon,
        web_openings,
    )
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="bolt_line_openings",
        status="observed" if web_openings else "not_applicable",
        title_zh="闭合螺栓轮廓切口",
        summary_zh=(
            f"从 Bolt 闭合线链恢复 {len(web_openings)} 个腹板异形孔"
            if web_openings
            else "腹板投影中没有可验证的 Bolt 闭合线链"
        ),
        hypothesis_id=hypothesis_id,
        shapes=polygon_shapes(
            "web-bolt-line-opening",
            "manufacturing_cut",
            [opening.geometry for opening in web_openings],
        ),
        payload={
            "opening_count": len(web_openings),
            "source_blocks": web_opening_blocks,
            "recognition_rule": "closed_bolt_line_chain_inside_selected_plate",
        },
    )
    web_plate = _normalize_polygon_plate(
        web_manufacturing_polygon,
        entities=main.entities,
        role=BHPlateRole.WEB,
        thickness=metadata.profile.web_thickness,
        label=canonical_bh_label(metadata.part_number, "web"),
        quantity=1,
        cuts=web_cuts,
        owned_openings=web_openings,
        grid_size=web_result.grid_size,
        provenance={
            "source_view_role": "web_projection",
            "source_block": main.name,
            "source_insert_handle": main.handle,
            **source_view_trace(main),
            "selection": {
                **web_result.diagnostics,
                "projection_cell_interiors": projection_cell_diagnostics,
            },
            "cut_source_blocks": web_hole_blocks,
            "polygonal_cut_source_blocks": web_opening_blocks,
            **_entity_trace(main.entities),
        },
        observer=observer,
        hypothesis_id=hypothesis_id,
    )

    web_axis = choose_long_axis(main.bbox, metadata.nominal_length)
    face_main_flange_spans = _main_flange_side_spans(
        web_result.all_faces,
        web_result.polygon,
        long_axis=web_axis,
        flange_thickness=metadata.profile.flange_thickness,
        nominal_length=metadata.nominal_length,
    )
    source_course_spans = _source_backed_main_flange_side_spans(
        main.entities,
        web_result.polygon,
        long_axis=web_axis,
        flange_thickness=metadata.profile.flange_thickness,
        nominal_length=metadata.nominal_length,
        manufacturing_tolerance_mm=manufacturing_tolerance_mm,
    )
    course_span_values = tuple(sorted(source_course_spans.values()))
    complete_source_course_spans = (
        set(source_course_spans) == {"low", "high"}
    )
    distinct_source_course_spans = (
        len(course_span_values) == 2
        and (course_span_values[1] - course_span_values[0])
        > max(5.0, 0.005 * metadata.nominal_length)
    )
    equivalent_source_course_spans = (
        len(course_span_values) == 2
        and (course_span_values[1] - course_span_values[0])
        <= manufacturing_tolerance_mm
    )
    face_span_values = tuple(sorted(face_main_flange_spans.values()))
    distinct_face_spans = (
        len(face_span_values) == 2
        and (face_span_values[1] - face_span_values[0])
        > max(5.0, 0.005 * metadata.nominal_length)
    )
    flange_polygons, flange_grid, flange_selection = select_flange_polygons(
        flange.entities,
        entity_source_ids=flange.entity_source_ids,
        annotation_masks=_projection_annotation_masks(instances),
        flange_width=metadata.profile.flange_width,
        nominal_length=metadata.nominal_length,
        source_bbox=flange.bbox,
        # Physical/nested flange selection keeps its established face-backed
        # evidence.  Source-course recovery is a later development fallback;
        # feeding it back here could flatten a genuine chamfered source plate.
        main_flange_spans=face_main_flange_spans,
        manufacturing_tolerance_mm=manufacturing_tolerance_mm,
        observer=observer,
        hypothesis_id=hypothesis_id,
    )

    web_bounds = web_result.polygon.bounds
    actual_web_transverse = (
        web_bounds[3] - web_bounds[1] if web_axis == "x" else web_bounds[2] - web_bounds[0]
    )
    flange_axis = choose_long_axis(flange.bbox, metadata.nominal_length)
    first_bounds = flange_polygons[0].bounds
    source_projection_length = (
        first_bounds[2] - first_bounds[0] if flange_axis == "x" else first_bounds[3] - first_bounds[1]
    )
    source_projection_overrun = (
        complete_source_course_spans
        and source_projection_length - max(course_span_values)
        > max(5.0, 0.005 * metadata.nominal_length)
    )
    equal_course_outer_endpoint_spans: dict[str, float] = {}
    if (
        equivalent_source_course_spans
        and source_projection_overrun
        and flange_development_policy.authorizes_profile(development_profile_id)
    ):
        raw_outer_spans = _source_backed_main_flange_side_spans(
            main.entities,
            web_result.polygon,
            long_axis=web_axis,
            flange_thickness=metadata.profile.flange_thickness,
            nominal_length=metadata.nominal_length,
            manufacturing_tolerance_mm=manufacturing_tolerance_mm,
            outer_endpoint_envelope=True,
        )
        if (
            set(raw_outer_spans) == {"low", "high"}
            and abs(raw_outer_spans["low"] - raw_outer_spans["high"])
            <= manufacturing_tolerance_mm
        ):
            quantized_outer_spans = {
                side: quantize_derived_flange_length(
                    value,
                    flange_development_policy,
                )
                for side, value in raw_outer_spans.items()
            }
            if (
                abs(quantized_outer_spans["low"] - quantized_outer_spans["high"])
                <= manufacturing_tolerance_mm
            ):
                equal_course_outer_endpoint_spans = quantized_outer_spans
    equal_course_outer_endpoint_recovery = bool(
        equal_course_outer_endpoint_spans
    )
    use_source_course_spans = (
        complete_source_course_spans
        and not distinct_face_spans
        and (
            distinct_source_course_spans
            or equal_course_outer_endpoint_recovery
        )
    )
    # A complete four-course source pattern is more stable than polygon faces
    # whose topology changes with precision-grid phase.  Different course
    # lengths prove two distinct plates.  For equivalent parallel bevel pairs,
    # measure only between the outside end points of each flange's two source
    # courses; the longer overall projection remains non-authoritative.
    development_source_course_spans = (
        equal_course_outer_endpoint_spans
        if equal_course_outer_endpoint_recovery
        else source_course_spans
    )
    main_flange_spans = (
        development_source_course_spans
        if use_source_course_spans
        else face_main_flange_spans
    )
    span_values = tuple(sorted(main_flange_spans.values()))
    distinct_main_flange_spans = (
        len(span_values) == 2
        and (span_values[1] - span_values[0])
        > max(5.0, 0.005 * metadata.nominal_length)
    )
    equal_course_projection_recovery = (
        use_source_course_spans
        and equal_course_outer_endpoint_recovery
    )
    needs_development = (
        metadata.profile.is_variable_height
        or actual_web_transverse > metadata.profile.max_height + 1.0
        or distinct_main_flange_spans
        or equal_course_projection_recovery
    )
    development = None
    source_flange_polygons = flange_polygons[:]
    if needs_development and len(flange_polygons) == 1:
        development = estimate_flange_developments(
            all_faces=web_result.all_faces,
            web_polygon=web_result.polygon,
            source_bbox=main.bbox,
            nominal_length=metadata.nominal_length,
            flange_thickness=metadata.profile.flange_thickness,
            source_projection_length=source_projection_length,
            variable_height=metadata.profile.is_variable_height,
            source_course_spans=(
                development_source_course_spans
                if use_source_course_spans
                else None
            ),
            development_policy=flange_development_policy,
            development_profile_id=development_profile_id,
            manufacturing_tolerance_mm=manufacturing_tolerance_mm,
            observer=observer,
            hypothesis_id=hypothesis_id,
        )
        if development.mode == "constant_height_two_flange_paths":
            # Equal-height member whose two flange paths differ: the flanges
            # are flat rectangular plates (length x width).  Projection end
            # bevels/stepped edges are assembly detail, not plate outline, so
            # materialize clean rectangles instead of carrying the stepped
            # silhouette (left-end protrusion) into the plate.  The rectangle
            # is emitted in the source-projection frame (same origin as the
            # flange circles) so ``_normalize_polygon_plate`` applies one
            # shared translation to both outline and circles, keeping cut
            # provenance registrable.  The long edge follows ``flange_axis``
            # so a vertical member (long axis = y) is not rotated 90 deg.
            src_b = source_flange_polygons[0].bounds
            if flange_axis == "x":
                flange_polygons = [
                    box(
                        src_b[0],
                        src_b[1],
                        src_b[0] + target,
                        src_b[1] + metadata.profile.flange_width,
                    )
                    for target in development.target_lengths
                ]
            else:
                flange_polygons = [
                    box(
                        src_b[0],
                        src_b[1],
                        src_b[0] + metadata.profile.flange_width,
                        src_b[1] + target,
                    )
                    for target in development.target_lengths
                ]
        else:
            developed: list[Polygon] = []
            for target in development.target_lengths:
                polygon = extend_flange_polygon_to_length(
                    flange_polygons[0],
                    target,
                    long_axis=flange_axis,
                    observer=observer,
                    hypothesis_id=hypothesis_id,
                )
                if any(polygon.hausdorff_distance(existing) <= 0.05 for existing in developed):
                    continue
                developed.append(polygon)
            if developed:
                flange_polygons = developed
    else:
        reason = (
            "当前构件为等高直梁，翼缘直接采用投影视图。"
            if not needs_development
            else "已识别多个独立翼缘轮廓，不建立单投影展开映射。"
        )
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_development",
            status="not_applicable",
            title_zh="翼缘展开长度推断",
            summary_zh=reason,
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes("flange-projection", "face_selected", flange_polygons),
            payload={
                "reason": "direct_projection" if not needs_development else "multiple_source_geometries",
                "source_projection_length_mm": source_projection_length,
            },
        )
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_rigid_extension",
            status="not_applicable",
            title_zh="翼缘刚性延长",
            summary_zh="未产生新的展开目标长度，因此不执行刚性延长。",
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes("flange-no-rigid-extension", "face_selected", flange_polygons),
            payload={"reason": "no_development_target"},
        )

    # A direct flange projection can include a short end return that belongs
    # to the assembled view rather than to the flat plate.  Rectangularize it
    # only when the complete projection already supplies the fabrication
    # length, the full profile width is present, and a main-view flange band
    # independently reaches the same longitudinal extent.
    if development is None and len(flange_polygons) == 1:
        projected = flange_polygons[0]
        validated_rectangle = _validated_direct_projection_rectangle(
            projected=projected,
            entities=flange.entities,
            entity_source_ids=flange.entity_source_ids,
            projection_grid_mm=flange_grid,
            flange_axis=flange_axis,
            flange_width=metadata.profile.flange_width,
            main_flange_spans=main_flange_spans,
            manufacturing_tolerance_mm=manufacturing_tolerance_mm,
        )
        if validated_rectangle is not None:
            flange_polygons = [validated_rectangle]

    # Circles are classified on the source projection, where their centres
    # live.  A developed (rectangular) flange plate uses a local coordinate
    # frame, so circle ownership is decided from the source outline and then
    # re-split by developed length when two different-length plates exist.
    cut_polygons = source_flange_polygons
    flange_openings: list[OwnedPolygonalOpening] = []
    flange_opening_blocks: set[str] = set()
    for polygon in cut_polygons:
        collected, blocks = _collect_bolt_line_openings(
            instances,
            flange,
            polygon,
        )
        flange_opening_blocks.update(blocks)
        for opening in collected:
            if any(
                opening.geometry.hausdorff_distance(existing.geometry) <= 0.05
                for existing in flange_openings
            ):
                continue
            flange_openings.append(opening)
    flange_opening_assignments: list[list[OwnedPolygonalOpening]] = [
        [] for _ in cut_polygons
    ]
    for opening in flange_openings:
        covered = [
            index
            for index, polygon in enumerate(cut_polygons)
            if polygon.buffer(0.05).covers(opening.geometry)
        ]
        if len(covered) != 1:
            raise ValueError(
                "Polygonal flange opening does not have one unique source projection owner."
            )
        flange_opening_assignments[covered[0]].append(opening)
    flange_cut_assignments, split_single, flange_cut_diagnostics = _assign_flange_cuts(
        cut_polygons,
        flange_cuts,
        instances=instances,
        main=main,
        web_polygon=web_result.polygon,
        all_main_faces=web_result.all_faces,
        nominal_length=metadata.nominal_length,
        flange_thickness=metadata.profile.flange_thickness,
        main_long_axis=web_axis,
        flange_long_axis=flange_axis,
        main_flange_spans=main_flange_spans,
        polygonal_opening_count=len(flange_openings),
        observer=observer,
        hypothesis_id=hypothesis_id,
    )

    # Equal-height member whose two flange paths differ AND the flange carries
    # circles: re-split ownership by developed length so the plate pair keeps
    # its distinct lengths instead of silently collapsing to a same-length q2.
    if (
        development is not None
        and development.mode == "constant_height_two_flange_paths"
        and flange_cuts
        and len(flange_polygons) == 2
    ):
        developed_upper, developed_lower = _split_developed_flange_cuts(
            flange_cuts,
            development.target_lengths,
            long_axis=flange_axis,
            src_bounds=cut_polygons[0].bounds,
            side_counts=flange_cut_diagnostics.get(
                "main_bolt_line_symbol_counts", {}
            ),
        )
        flange_cut_assignments = [developed_upper, developed_lower]
        split_single = False

    flange_plates: list[BHPlate] = []
    if len(flange_polygons) == 1 and not split_single:
        owned_flange_openings = flange_opening_assignments[0]
        flange_plates.append(
            _normalize_polygon_plate(
                _subtract_owned_openings(
                    flange_polygons[0], owned_flange_openings
                ),
                entities=flange.entities,
                role=BHPlateRole.FLANGE,
                thickness=metadata.profile.flange_thickness,
                label=canonical_bh_label(metadata.part_number, "flange", quantity=2),
                quantity=2,
                cuts=flange_cut_assignments[0],
                owned_openings=owned_flange_openings,
                source_index=1,
                grid_size=flange_grid,
                provenance={
                    "source_view_role": "flange_projection",
                    "source_block": flange.name,
                    "source_insert_handle": flange.handle,
                    **source_view_trace(flange),
                    "source_index": 1,
                    "selection": flange_selection,
                    "cut_source_blocks": flange_hole_blocks,
                    "polygonal_cut_source_blocks": sorted(flange_opening_blocks),
                    **_entity_trace(flange.entities),
                },
                observer=observer,
                hypothesis_id=hypothesis_id,
            )
        )
    elif len(flange_polygons) == 1 and split_single:
        for index, (cuts, owned_flange_openings) in enumerate(
            (
                (flange_cut_assignments[0], flange_opening_assignments[0]),
                ([], []),
            ),
            start=1,
        ):
            flange_plates.append(
                _normalize_polygon_plate(
                    _subtract_owned_openings(
                        flange_polygons[0], owned_flange_openings
                    ),
                    entities=flange.entities,
                    role=BHPlateRole.FLANGE,
                    thickness=metadata.profile.flange_thickness,
                    label=canonical_bh_label(metadata.part_number, "flange", index=index),
                    quantity=1,
                    cuts=cuts,
                    owned_openings=owned_flange_openings,
                    source_index=index,
                    grid_size=flange_grid,
                    provenance={
                        "source_view_role": "flange_projection",
                        "source_block": flange.name,
                        "source_insert_handle": flange.handle,
                        **source_view_trace(flange),
                        "source_index": index,
                        "selection": flange_selection,
                        "cut_source_blocks": flange_hole_blocks,
                        "polygonal_cut_source_blocks": sorted(
                            flange_opening_blocks
                        ),
                        **_entity_trace(flange.entities),
                    },
                    observer=observer,
                    hypothesis_id=hypothesis_id,
                )
            )
    else:
        for index, polygon in enumerate(flange_polygons, start=1):
            owned_flange_openings = (
                flange_opening_assignments[index - 1]
                if index - 1 < len(flange_opening_assignments)
                else []
            )
            flange_plates.append(
                _normalize_polygon_plate(
                    _subtract_owned_openings(polygon, owned_flange_openings),
                    entities=flange.entities,
                    role=BHPlateRole.FLANGE,
                    thickness=metadata.profile.flange_thickness,
                    label=canonical_bh_label(metadata.part_number, "flange", index=index),
                    quantity=1,
                    cuts=flange_cut_assignments[index - 1] if index - 1 < len(flange_cut_assignments) else [],
                    owned_openings=owned_flange_openings,
                    source_index=index,
                    grid_size=flange_grid,
                    provenance={
                        "source_view_role": "flange_projection",
                        "source_block": flange.name,
                        "source_insert_handle": flange.handle,
                        **source_view_trace(flange),
                        "source_index": index,
                        "selection": flange_selection,
                        "cut_source_blocks": flange_hole_blocks,
                        "polygonal_cut_source_blocks": sorted(
                            flange_opening_blocks
                        ),
                        **_entity_trace(flange.entities),
                    },
                    observer=observer,
                    hypothesis_id=hypothesis_id,
                )
            )

    total_cuts = sum(len(plate.circular_cuts) for plate in [web_plate, *flange_plates])
    if development:
        development_payload: dict[str, object] = {
            "mode": development.mode,
            "target_lengths_mm": list(development.target_lengths),
            "raw_lengths_mm": list(development.raw_lengths),
            "source_projection_length_mm": development.source_projection_length,
            "details": list(development.details),
            "certificate": dict(development.certificate),
            "source_view": source_view_trace(main),
        }
        source_view = development_payload["source_view"]
        certificate = development_payload["certificate"]
        if isinstance(source_view, dict) and isinstance(certificate, dict):
            source_ids = tuple(source_view.get("source_entity_ids", ()) or ())
            certificate["source_entity_count"] = len(source_ids)
            certificate["authorized"] = bool(
                certificate.get("authorized") and source_ids
            )
    else:
        development_payload = {
            "mode": "projection_view",
            "target_lengths_mm": [source_projection_length],
            "source_projection_length_mm": source_projection_length,
            "source_view": source_view_trace(flange),
            "certificate": {
                "authorized": True,
                "certificate_kind": "direct_source_projection",
                "policy": {
                    "preserve_direct_projection": True,
                },
            },
        }
    development_payload["semantic_assessment"] = (
        assess_flange_development_semantics(
            development_payload,
            nominal_part_length_mm=metadata.nominal_length,
            flange_thickness_mm=metadata.profile.flange_thickness,
            geometric_tolerance_mm=manufacturing_tolerance_mm,
        )
    )
    projection_boundary_assessments: list[dict[str, object]] = []
    web_boundary_repairs = web_result.diagnostics.get(
        "projection_boundary_repairs",
        (),
    )
    if isinstance(web_boundary_repairs, (list, tuple)):
        projection_boundary_assessments.extend(
            item for item in web_boundary_repairs if isinstance(item, dict)
        )
    boundary_completion = web_result.diagnostics.get("boundary_completion", {})
    if isinstance(boundary_completion, dict):
        regularization = boundary_completion.get("regularization")
        if isinstance(regularization, dict) and regularization.get("repair_kind"):
            projection_boundary_assessments.append(regularization)
    flange_boundary_repairs = flange_selection.get(
        "projection_boundary_repairs",
        (),
    )
    if isinstance(flange_boundary_repairs, (list, tuple)):
        projection_boundary_assessments.extend(
            item for item in flange_boundary_repairs if isinstance(item, dict)
        )
    web_boundary_assessment = web_result.diagnostics.get(
        "projection_boundary_conservation"
    )
    if isinstance(web_boundary_assessment, dict):
        projection_boundary_assessments.append(web_boundary_assessment)
    flange_boundary_conservation = flange_selection.get(
        "projection_boundary_conservation",
        (),
    )
    if isinstance(flange_boundary_conservation, (list, tuple)):
        projection_boundary_assessments.extend(
            item
            for item in flange_boundary_conservation
            if isinstance(item, dict)
        )
    diagnostics = {
        "profile_family": "BH",
        "main_view_block": main.name,
        "flange_view_block": flange.name,
        "web_hole_blocks": web_hole_blocks,
        "flange_hole_blocks": flange_hole_blocks,
        "web_polygon_grid_mm": web_result.grid_size,
        "web_selection": web_result.diagnostics,
        "flange_polygon_grid_mm": flange_grid,
        "flange_selection": flange_selection,
        "web_outer_bbox_source": list(web_result.polygon.bounds),
        "web_inner_contours": len(web_plate.inner_contours),
        "web_bolt_circle_count": len(web_plate.circular_cuts),
        "flange_bolt_circle_count": sum(len(plate.circular_cuts) for plate in flange_plates),
        "total_cut_circle_count": total_cuts,
        "flange_cut_assignment": flange_cut_diagnostics,
        "flange_component_count": len(flange_plates),
        "profile_variable_height": metadata.profile.is_variable_height,
        "profile_secondary_height_mm": metadata.profile.secondary_height,
        "flange_development": development_payload,
        "projection_source_edge_conservation": {
            "assessments": projection_boundary_assessments,
        },
        "expected_clear_web_height_mm": metadata.profile.clear_web_height,
        "actual_web_bbox_mm": [web_plate.bbox.width, web_plate.bbox.height],
        "cross_line_policy": "Bolt LINE/XLINE/RAY entities are semantic auxiliaries only and are never emitted.",
    }
    if compiler_diagnostics:
        diagnostics["compiler"] = compiler_diagnostics
    assembly = BHAssembly(
        metadata=metadata,
        web_plate=web_plate,
        flange_plates=flange_plates,
        retained_insert_handles=_retained_handles(instances, metadata.profile.raw_text),
        diagnostics=diagnostics,
    )
    manufacturing_shapes = []
    for index, plate in enumerate(assembly.plates, start=1):
        manufacturing_shapes.append(
            contour_shape(
                f"manufacturing-plate-{index:02d}", "manufacturing_plate", plate.contour
            )
        )
        manufacturing_shapes.extend(
            cut_shapes(
                f"manufacturing-cut-{index:02d}",
                "manufacturing_cut",
                plate.circular_cuts,
            )
        )
        manufacturing_shapes.extend(
            contour_shape(
                f"manufacturing-opening-{index:02d}-{opening_index:02d}",
                "manufacturing_cut",
                contour,
            )
            for opening_index, contour in enumerate(plate.inner_contours, start=1)
        )
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="candidate_manufacturing_ir",
        status="observed",
        title_zh="候选制造 IR",
        summary_zh=f"候选降低为 {len(assembly.plates)} 个制造板件记录",
        hypothesis_id=hypothesis_id,
        shapes=tuple(manufacturing_shapes),
        payload={
            "part_number": metadata.part_number,
            "plate_count": len(assembly.plates),
            "plates": [
                {
                    "label": plate.label,
                    "role": plate.role.value,
                    "quantity": plate.quantity,
                    "thickness_mm": plate.thickness,
                    "bbox_mm": [plate.bbox.width, plate.bbox.height],
                    "circular_cut_count": len(plate.circular_cuts),
                    "inner_opening_count": len(plate.inner_contours),
                }
                for plate in assembly.plates
            ],
        },
    )
    return assembly
