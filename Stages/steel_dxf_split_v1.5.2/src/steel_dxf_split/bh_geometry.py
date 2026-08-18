from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import atan2, ceil, cos, degrees, hypot, radians, sin, tan
from typing import Iterable

from ezdxf.entities import Arc, DXFEntity
from shapely import set_precision
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, unary_union

from .bh_models import BulgeContour, BulgeVertex
from .bh_development import (
    quantize_derived_flange_length,
    select_profile_authorized_cranked_candidate,
)
from .bh_ir import SourceViewRef
from .bh_knowledge import BHFlangeDevelopmentPolicy
from .bh_projection_semantics import (
    BoundaryRepairDecision,
    ProjectionBoundarySemantics,
    assess_selected_projection_boundary,
    analyse_projection_boundary,
    evaluate_boundary_repair,
    evaluate_longitudinal_projection_overlay_repair,
)
from .bh_trace import TraceObserver, emit_trace
from .bh_trace_geometry import polygon_shape, polygon_shapes
from .geometry_types import BoundingBox, Point2D


@dataclass(slots=True)
class PartBlock:
    insert: DXFEntity
    entities: list[DXFEntity]
    bbox: BoundingBox
    source_view: SourceViewRef | None = None
    entity_source_ids: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.insert.dxf.name

    @property
    def handle(self) -> str:
        return self.insert.dxf.handle or ""

    @property
    def region_id(self) -> str | None:
        return self.source_view.region_id if self.source_view is not None else None


@dataclass(frozen=True, slots=True)
class SourceArc:
    center: Point2D
    radius: float
    start_angle: float
    end_angle: float
    start: Point2D
    end: Point2D

    @property
    def sweep(self) -> float:
        return (self.end_angle - self.start_angle) % 360.0


@dataclass(slots=True)
class PolygonizedResult:
    polygon: Polygon
    grid_size: float
    all_faces: list[Polygon]
    diagnostics: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProjectionAnnotationMask:
    """One Tekla annotation group that may mask a drawn projection edge.

    Tekla can export continuous part lines as two fragments when ``Cut lines
    with text`` is enabled.  Keeping the annotation entities grouped prevents
    unrelated drawing text from becoming permission to close arbitrary gaps.
    """

    semantic_layer: str
    entities: tuple[DXFEntity, ...]
    source_ids: tuple[str, ...] = ()


def arc_points(entity: Arc, max_angle_step: float = 1.0) -> list[tuple[float, float]]:
    start_angle = float(entity.dxf.start_angle)
    sweep = (float(entity.dxf.end_angle) - start_angle) % 360.0
    count = max(4, int(ceil(sweep / max_angle_step)))
    center = entity.dxf.center
    radius = float(entity.dxf.radius)
    return [
        (
            float(center.x) + radius * cos(radians(start_angle + sweep * index / count)),
            float(center.y) + radius * sin(radians(start_angle + sweep * index / count)),
        )
        for index in range(count + 1)
    ]


def entity_points(entity: DXFEntity) -> list[Point2D]:
    if entity.dxftype() == "LINE":
        return [
            Point2D(float(entity.dxf.start.x), float(entity.dxf.start.y)),
            Point2D(float(entity.dxf.end.x), float(entity.dxf.end.y)),
        ]
    if entity.dxftype() == "ARC":
        return [Point2D(x, y) for x, y in arc_points(entity, max_angle_step=5.0)]
    return []


def entities_bbox(entities: Iterable[DXFEntity]) -> BoundingBox:
    points = [point for entity in entities for point in entity_points(entity)]
    return BoundingBox.from_points(points)


def solid_part_entities(entities: Iterable[DXFEntity]) -> list[DXFEntity]:
    result: list[DXFEntity] = []
    for entity in entities:
        if entity.dxf.layer != "Part" or entity.dxftype() not in {"LINE", "ARC"}:
            continue
        if entity.dxf.linetype == "XKITLINE04":
            continue
        if entity.dxftype() == "LINE":
            start = entity.dxf.start
            end = entity.dxf.end
            if hypot(float(end.x - start.x), float(end.y - start.y)) <= 0.05:
                continue
        result.append(entity)
    return result


def polygonize_part_entities(
    entities: list[DXFEntity],
    grid_size: float,
    *,
    inferred_linework: Iterable[LineString] = (),
) -> list[Polygon]:
    linework: list[LineString] = []
    for entity in solid_part_entities(entities):
        if entity.dxftype() == "LINE":
            linework.append(
                LineString(
                    [
                        (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                        (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                    ]
                )
            )
        else:
            linework.append(LineString(arc_points(entity)))
    linework.extend(inferred_linework)
    if not linework:
        return []
    noded = unary_union(
        set_precision(MultiLineString(linework), grid_size, mode="valid_output")
    )
    return list(polygonize(noded))


def _axis_coordinates(
    point: tuple[float, float],
    long_axis: str,
) -> tuple[float, float]:
    return point if long_axis == "x" else (point[1], point[0])


def _ordered_longitudinal_line(
    entity: DXFEntity,
    *,
    long_axis: str,
    transverse_tolerance_mm: float,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if entity.dxftype() != "LINE":
        return None
    start = (float(entity.dxf.start.x), float(entity.dxf.start.y))
    end = (float(entity.dxf.end.x), float(entity.dxf.end.y))
    start_long, start_transverse = _axis_coordinates(start, long_axis)
    end_long, end_transverse = _axis_coordinates(end, long_axis)
    longitudinal_span = abs(end_long - start_long)
    if longitudinal_span <= transverse_tolerance_mm:
        return None
    # Preserve a real shallow course instead of forcing it onto a global axis.
    # This predicate only excludes lines whose role is transverse/end-cap.
    if abs(end_transverse - start_transverse) > max(
        transverse_tolerance_mm,
        longitudinal_span * 1e-4,
    ):
        return None
    return (start, end) if start_long <= end_long else (end, start)


def _has_projection_end_cap(
    entities: list[DXFEntity],
    *,
    long_axis: str,
    longitudinal_value: float,
    flange_width: float,
    tolerance_mm: float,
) -> bool:
    for entity in solid_part_entities(entities):
        if entity.dxftype() != "LINE":
            continue
        start = (float(entity.dxf.start.x), float(entity.dxf.start.y))
        end = (float(entity.dxf.end.x), float(entity.dxf.end.y))
        start_long, start_transverse = _axis_coordinates(start, long_axis)
        end_long, end_transverse = _axis_coordinates(end, long_axis)
        if (
            abs(start_long - longitudinal_value) <= tolerance_mm
            and abs(end_long - longitudinal_value) <= tolerance_mm
            and abs(end_transverse - start_transverse) >= 0.80 * flange_width
        ):
            return True
    return False


def _text_position_and_height(entity: DXFEntity) -> tuple[tuple[float, float], float] | None:
    if entity.dxftype() == "TEXT":
        insert = entity.dxf.insert
        return (float(insert.x), float(insert.y)), float(entity.dxf.height)
    if entity.dxftype() == "MTEXT":
        insert = entity.dxf.insert
        return (float(insert.x), float(insert.y)), float(entity.dxf.char_height)
    return None


def _annotation_group_covers_gap(
    mask: ProjectionAnnotationMask,
    bridge: LineString,
    *,
    long_axis: str,
) -> bool:
    bridge_start = _axis_coordinates(tuple(bridge.coords[0]), long_axis)
    bridge_end = _axis_coordinates(tuple(bridge.coords[-1]), long_axis)
    gap_min, gap_max = sorted((bridge_start[0], bridge_end[0]))
    gap_length = gap_max - gap_min
    if gap_length <= 1e-9:
        return False

    texts = [
        observation
        for entity in mask.entities
        if (observation := _text_position_and_height(entity)) is not None
    ]
    if not texts:
        return False

    mark_lines = [
        LineString(
            (
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            )
        )
        for entity in mask.entities
        if entity.dxftype() == "LINE"
    ]
    for position, height in texts:
        if height <= 0.0:
            continue
        text_long, _ = _axis_coordinates(position, long_axis)
        if not gap_min - height <= text_long <= gap_max + height:
            continue
        if Point(position).distance(bridge) > 2.5 * height:
            continue
        for mark_line in mark_lines:
            first = _axis_coordinates(tuple(mark_line.coords[0]), long_axis)
            second = _axis_coordinates(tuple(mark_line.coords[-1]), long_axis)
            mark_min, mark_max = sorted((first[0], second[0]))
            overlap = max(0.0, min(gap_max, mark_max) - max(gap_min, mark_min))
            if overlap < 0.80 * gap_length:
                continue
            if mark_line.distance(bridge) > 2.5 * height:
                continue
            return True
    return False


def _annotation_masked_projection_bridges(
    entities: list[DXFEntity],
    *,
    annotation_masks: tuple[ProjectionAnnotationMask, ...],
    long_axis: str,
    flange_width: float,
    source_bbox: BoundingBox,
    alignment_tolerance_mm: float = 0.05,
) -> tuple[list[LineString], list[dict[str, object]]]:
    """Infer only source gaps proved to be Tekla annotation masks.

    A bridge needs two collinear longitudinal fragments on a silhouette, a
    full opposite course, both member end caps, and text plus linework from a
    single recognized annotation group covering the gap.  Consequently an
    unexplained opening, bevel, notch, or nearby free text fails closed.
    """

    if not annotation_masks:
        return [], []
    source_entities = solid_part_entities(entities)
    courses = [
        (entity, ordered)
        for entity in source_entities
        if (
            ordered := _ordered_longitudinal_line(
                entity,
                long_axis=long_axis,
                transverse_tolerance_mm=alignment_tolerance_mm,
            )
        )
        is not None
    ]
    source_long_bounds = (
        (source_bbox.min_x, source_bbox.max_x)
        if long_axis == "x"
        else (source_bbox.min_y, source_bbox.max_y)
    )
    source_transverse_bounds = (
        (source_bbox.min_y, source_bbox.max_y)
        if long_axis == "x"
        else (source_bbox.min_x, source_bbox.max_x)
    )
    if not all(
        _has_projection_end_cap(
            source_entities,
            long_axis=long_axis,
            longitudinal_value=value,
            flange_width=flange_width,
            tolerance_mm=alignment_tolerance_mm,
        )
        for value in source_long_bounds
    ):
        return [], []

    bridges: list[LineString] = []
    diagnostics: list[dict[str, object]] = []
    for first_index, (first_entity, first) in enumerate(courses):
        first_start = _axis_coordinates(first[0], long_axis)
        first_end = _axis_coordinates(first[1], long_axis)
        for second_entity, second in courses[first_index + 1 :]:
            second_start = _axis_coordinates(second[0], long_axis)
            second_end = _axis_coordinates(second[1], long_axis)
            if second_start[0] < first_start[0]:
                left_end, right_start = (
                    second_end,
                    first_start,
                )
                bridge_coordinates = (second[1], first[0])
            else:
                left_end, right_start = (
                    first_end,
                    second_start,
                )
                bridge_coordinates = (first[1], second[0])
            if right_start[0] - left_end[0] <= alignment_tolerance_mm:
                continue
            if abs(right_start[1] - left_end[1]) > alignment_tolerance_mm:
                continue
            course_transverse = 0.5 * (left_end[1] + right_start[1])
            if min(
                abs(course_transverse - source_transverse_bounds[0]),
                abs(course_transverse - source_transverse_bounds[1]),
            ) > alignment_tolerance_mm:
                continue
            gap = (left_end[0], right_start[0])
            opposite_supported = any(
                candidate is not first_entity
                and candidate is not second_entity
                and candidate_ordered[0]
                <= gap[0] + alignment_tolerance_mm
                and candidate_ordered[2]
                >= gap[1] - alignment_tolerance_mm
                and abs(candidate_ordered[1] - course_transverse)
                >= 0.80 * flange_width
                for candidate, ordered in courses
                for candidate_ordered in [
                    (
                        _axis_coordinates(ordered[0], long_axis)[0],
                        0.5
                        * (
                            _axis_coordinates(ordered[0], long_axis)[1]
                            + _axis_coordinates(ordered[1], long_axis)[1]
                        ),
                        _axis_coordinates(ordered[1], long_axis)[0],
                    )
                ]
            )
            if not opposite_supported:
                continue
            bridge = LineString(bridge_coordinates)
            supporting_mask = next(
                (
                    mask
                    for mask in annotation_masks
                    if _annotation_group_covers_gap(mask, bridge, long_axis=long_axis)
                ),
                None,
            )
            if supporting_mask is None:
                continue
            if any(bridge.equals_exact(existing, 1e-9) for existing in bridges):
                continue
            bridges.append(bridge)
            diagnostics.append(
                {
                    "repair_kind": "annotation_masked_projection_gap",
                    "semantic_layer": supporting_mask.semantic_layer,
                    "annotation_source_ids": list(supporting_mask.source_ids),
                    "gap_length_mm": round(float(bridge.length), 6),
                    "bridge": [
                        [round(float(x), 6), round(float(y), 6)]
                        for x, y in bridge.coords
                    ],
                }
            )
    return bridges, diagnostics


def _axis_lengths(bounds: tuple[float, float, float, float], long_axis: str) -> tuple[float, float]:
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    return (width, height) if long_axis == "x" else (height, width)


def choose_long_axis(bbox: BoundingBox, nominal_length: float) -> str:
    return "x" if abs(bbox.width - nominal_length) <= abs(bbox.height - nominal_length) else "y"


def _near_full_flange_width(
    face: Polygon,
    *,
    long_axis: str,
    flange_width: float,
) -> bool:
    _, transverse = _axis_lengths(face.bounds, long_axis)
    return 0.80 * flange_width <= transverse <= 1.20 * flange_width


def _unchanged_boundary_decision(
    polygon: Polygon,
    *,
    reason: str,
    repair_kind: str,
    semantics: ProjectionBoundarySemantics,
    fidelity_tolerance_mm: float,
) -> BoundaryRepairDecision:
    return BoundaryRepairDecision(
        polygon=polygon,
        applied=False,
        reason=reason,
        repair_kind=repair_kind,
        protected_source_ids=semantics.protected_source_ids,
        lost_source_ids=(),
        fidelity_tolerance_mm=fidelity_tolerance_mm,
    )


def _candidate_sides_have_direct_support(
    candidate: Polygon,
    semantics: ProjectionBoundarySemantics,
    *,
    fidelity_tolerance_mm: float,
) -> bool:
    if not semantics.direct_edges:
        return False
    source_boundary = unary_union([edge.line for edge in semantics.direct_edges])
    source_band = source_boundary.buffer(
        fidelity_tolerance_mm,
        cap_style=2,
        join_style=2,
    )
    coordinates = list(candidate.exterior.coords)
    for start, end in zip(coordinates, coordinates[1:]):
        side = LineString((start, end))
        outside = side.difference(source_band)
        if not outside.is_empty and outside.length > max(
            1e-12,
            fidelity_tolerance_mm * 1e-3,
        ):
            return False
    return True


def _reconstruct_proven_rectangular_projection(
    polygon: Polygon,
    entities: list[DXFEntity],
    *,
    entity_source_ids: tuple[str, ...] = (),
    endpoint_tolerance_mm: float = 0.15,
    fidelity_tolerance_mm: float = 1e-7,
) -> BoundaryRepairDecision:
    """Recover polygonization extrema only when source edges prove a rectangle.

    Nearby endpoints are evidence for a possible precision repair, not a shape
    classifier.  The candidate is accepted only when every direct source edge
    survives and the source geometry supports all four candidate sides.
    """
    repair_kind = "proven_rectangular_projection"
    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=entity_source_ids,
        association_tolerance_mm=endpoint_tolerance_mm,
    )
    candidates: list[Point2D] = []
    expanded = polygon.buffer(endpoint_tolerance_mm)
    for entity in solid_part_entities(entities):
        if entity.dxftype() != "LINE":
            continue
        for point in (
            Point2D(float(entity.dxf.start.x), float(entity.dxf.start.y)),
            Point2D(float(entity.dxf.end.x), float(entity.dxf.end.y)),
        ):
            if expanded.covers(Point(point.x, point.y)):
                candidates.append(point)
    if not candidates:
        return _unchanged_boundary_decision(
            polygon,
            reason="no_nearby_source_endpoints",
            repair_kind=repair_kind,
            semantics=semantics,
            fidelity_tolerance_mm=fidelity_tolerance_mm,
        )
    exact_min_x = min(point.x for point in candidates)
    exact_max_x = max(point.x for point in candidates)
    exact_min_y = min(point.y for point in candidates)
    exact_max_y = max(point.y for point in candidates)
    # Only rectify shapes already very close to a rectangle.
    rectangle_area = (exact_max_x - exact_min_x) * (exact_max_y - exact_min_y)
    if rectangle_area <= 0 or polygon.area / rectangle_area < 0.985:
        return _unchanged_boundary_decision(
            polygon,
            reason="rectangle_fill_precondition_failed",
            repair_kind=repair_kind,
            semantics=semantics,
            fidelity_tolerance_mm=fidelity_tolerance_mm,
        )
    candidate = Polygon(
        [
            (exact_min_x, exact_min_y),
            (exact_max_x, exact_min_y),
            (exact_max_x, exact_max_y),
            (exact_min_x, exact_max_y),
        ]
    )
    decision = evaluate_boundary_repair(
        polygon,
        candidate,
        semantics,
        fidelity_tolerance_mm=fidelity_tolerance_mm,
        repair_kind=repair_kind,
    )
    if not decision.applied:
        return decision
    if not _candidate_sides_have_direct_support(
        candidate,
        semantics,
        fidelity_tolerance_mm=fidelity_tolerance_mm,
    ):
        return _unchanged_boundary_decision(
            polygon,
            reason="candidate_side_not_source_supported",
            repair_kind=repair_kind,
            semantics=semantics,
            fidelity_tolerance_mm=fidelity_tolerance_mm,
        )
    return decision



@dataclass(frozen=True, slots=True)
class FlangeDevelopmentEstimate:
    mode: str
    target_lengths: tuple[float, ...]
    raw_lengths: tuple[float, ...]
    source_projection_length: float
    details: tuple[dict[str, object], ...]
    certificate: dict[str, object]


def _segment_angle_mod_180(first: tuple[float, float], second: tuple[float, float]) -> float:
    return degrees(atan2(second[1] - first[1], second[0] - first[0])) % 180.0


def _angle_distance_180(first: float, second: float) -> float:
    delta = abs(first - second) % 180.0
    return min(delta, 180.0 - delta)


def _ring_path_length(
    coordinates: list[tuple[float, float]],
    start_index: int,
    end_index: int,
    step: int,
) -> float:
    count = len(coordinates)
    index = start_index
    total = 0.0
    while index != end_index:
        next_index = (index + step) % count
        total += hypot(
            coordinates[next_index][0] - coordinates[index][0],
            coordinates[next_index][1] - coordinates[index][1],
        )
        index = next_index
    return total


def _face_development_measurements(
    polygon: Polygon,
    *,
    long_axis: str,
    flange_thickness: float,
) -> dict[str, object]:
    coordinates = [(float(x), float(y)) for x, y in list(polygon.exterior.coords)[:-1]]
    if len(coordinates) < 3:
        raise ValueError("Flange face has fewer than three boundary vertices.")
    segments: list[tuple[float, float, float, float]] = []
    for first, second in zip(coordinates, coordinates[1:] + coordinates[:1]):
        dx = second[0] - first[0]
        dy = second[1] - first[1]
        length = hypot(dx, dy)
        if length > 1e-8:
            segments.append((length, dx / length, dy / length, _segment_angle_mod_180(first, second)))
    longest = max(segments, key=lambda item: item[0])
    long_span, _ = _axis_lengths(polygon.bounds, long_axis)
    significant = [
        segment
        for segment in segments
        if segment[0] >= max(5.0 * flange_thickness, 0.08 * long_span)
    ]
    dominant_angle = longest[3]
    straight = bool(significant) and all(
        _angle_distance_180(segment[3], dominant_angle) <= 2.0
        for segment in significant
    )

    axis_values = [point[0] if long_axis == "x" else point[1] for point in coordinates]
    minimum_index = min(range(len(axis_values)), key=axis_values.__getitem__)
    maximum_index = max(range(len(axis_values)), key=axis_values.__getitem__)
    path_lengths = (
        _ring_path_length(coordinates, minimum_index, maximum_index, 1),
        _ring_path_length(coordinates, minimum_index, maximum_index, -1),
    )
    if straight:
        ux, uy = longest[1], longest[2]
        projections = [x * ux + y * uy for x, y in coordinates]
        raw_length = max(projections) - min(projections)
        normal_projections = [-x * uy + y * ux for x, y in coordinates]
        observed_strip_thickness = max(normal_projections) - min(
            normal_projections
        )
        rectangular_area = raw_length * observed_strip_thickness
        rectangular_fill_ratio = (
            float(polygon.area) / rectangular_area
            if rectangular_area > 1e-9
            else 0.0
        )
        method = "straight_strip_projection"
    else:
        raw_length = min(path_lengths)
        observed_strip_thickness = None
        rectangular_fill_ratio = None
        method = "kinked_strip_boundary_paths"
    return {
        "method": method,
        "straight": straight,
        "raw_length": raw_length,
        "path_lengths": path_lengths,
        "axis_angle_deg": dominant_angle,
        "observed_strip_thickness_mm": observed_strip_thickness,
        "rectangular_fill_ratio": rectangular_fill_ratio,
        "bounds": tuple(float(value) for value in polygon.bounds),
        "area": float(polygon.area),
    }


def _numeric_measurement(
    measurement: dict[str, object],
    key: str,
    *,
    default: float | None = None,
) -> float:
    value = measurement.get(key, default)
    if not isinstance(value, (int, float)):
        raise TypeError(f"Flange development measurement {key!r} is not numeric.")
    return float(value)


def _numeric_measurement_sequence(
    measurement: dict[str, object],
    key: str,
) -> tuple[float, ...]:
    value = measurement.get(key)
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, (int, float)) for item in value
    ):
        raise TypeError(
            f"Flange development measurement {key!r} is not a numeric sequence."
        )
    return tuple(float(item) for item in value)


def estimate_flange_developments(
    *,
    all_faces: list[Polygon],
    web_polygon: Polygon,
    source_bbox: BoundingBox,
    nominal_length: float,
    flange_thickness: float,
    source_projection_length: float,
    variable_height: bool,
    development_policy: BHFlangeDevelopmentPolicy,
    development_profile_id: str,
    manufacturing_tolerance_mm: float,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> FlangeDevelopmentEstimate:
    """Estimate developed flange lengths from the longitudinal main view.

    Two cases require development rather than direct projection copying:

    * variable-height members: upper and lower flange paths can have different
      developed lengths;
    * cranked/offset constant-height members: the projection is shorter than
      the sum of the true straight and inclined path segments.

    The function is geometry-driven and never reads the manual split file.
    """
    def traced(result: FlangeDevelopmentEstimate) -> FlangeDevelopmentEstimate:
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_development",
            status=("observed" if result.mode != "projection_only" else "not_applicable"),
            title_zh="翼缘展开长度推断",
            summary_zh=(
                f"{result.mode}: 目标长度 "
                + ", ".join(f"{value:.3f}" for value in result.target_lengths)
                + " mm"
            ),
            hypothesis_id=hypothesis_id,
            shapes=(
                polygon_shape("development-web", "face_selected", web_polygon),
                *polygon_shapes("development-face", "face_candidate", all_faces),
            ),
            payload={
                "mode": result.mode,
                "target_lengths_mm": result.target_lengths,
                "raw_lengths_mm": result.raw_lengths,
                "source_projection_length_mm": result.source_projection_length,
                "details": result.details,
                "certificate": result.certificate,
            },
        )
        return result

    long_axis = choose_long_axis(source_bbox, nominal_length)
    source_long, _ = _axis_lengths(source_bbox.as_tuple() if hasattr(source_bbox, "as_tuple") else (source_bbox.min_x, source_bbox.min_y, source_bbox.max_x, source_bbox.max_y), long_axis)
    web_center_transverse = web_polygon.centroid.y if long_axis == "x" else web_polygon.centroid.x
    minimum_area = max(100.0, flange_thickness * max(100.0, 0.02 * nominal_length))
    minimum_span = max(100.0, 0.30 * min(source_long, nominal_length))
    side_candidates: dict[int, list[Polygon]] = {-1: [], 1: []}
    for face in all_faces:
        # The selected web may have undergone sub-millimetre topology cleanup,
        # so exact GEOS equality is too brittle.  Exclude any face already
        # represented by the web material instead of allowing it to masquerade
        # as a flange path candidate.
        covered_by_web = (
            face.intersection(web_polygon.buffer(0.25, join_style=2)).area
            / max(face.area, 1.0)
        ) >= 0.98
        if covered_by_web or face.area < minimum_area:
            continue
        length, _ = _axis_lengths(face.bounds, long_axis)
        if length < minimum_span:
            continue
        transverse_center = face.centroid.y if long_axis == "x" else face.centroid.x
        side = 1 if transverse_center >= web_center_transverse else -1
        side_candidates[side].append(face)

    details: list[dict[str, object]] = []
    for side in (-1, 1):
        faces = side_candidates[side]
        if not faces:
            continue
        merged = unary_union(faces)
        components = list(merged.geoms) if isinstance(merged, MultiPolygon) else [merged]
        component = max(
            components,
            key=lambda polygon: (_axis_lengths(polygon.bounds, long_axis)[0], polygon.area),
        )
        measurement = _face_development_measurements(
            component,
            long_axis=long_axis,
            flange_thickness=flange_thickness,
        )
        measurement["side"] = "positive" if side > 0 else "negative"
        details.append(measurement)

    if len(details) < 2:
        return traced(FlangeDevelopmentEstimate(
            mode="projection_only",
            target_lengths=(source_projection_length,),
            raw_lengths=(source_projection_length,),
            source_projection_length=source_projection_length,
            details=tuple(details),
            certificate={
                "authorized": False,
                "certificate_kind": "not_applicable_projection_only",
                "policy": asdict(development_policy),
            },
        ))

    if variable_height:
        ordered_details = tuple(reversed(details))
        raw = tuple(
            _numeric_measurement(item, "raw_length")
            for item in ordered_details
        )
        strip_tolerance = max(
            float(manufacturing_tolerance_mm),
            0.02 * float(flange_thickness),
        )
        valid_straight_strips = tuple(
            bool(item.get("straight"))
            and item.get("method") == "straight_strip_projection"
            and item.get("observed_strip_thickness_mm") is not None
            and abs(
                _numeric_measurement(item, "observed_strip_thickness_mm")
                - float(flange_thickness)
            )
            <= strip_tolerance
            and _numeric_measurement(
                item,
                "rectangular_fill_ratio",
                default=0.0,
            )
            >= 0.98
            for item in ordered_details
        )
        profile_authorized = development_policy.authorizes_profile(
            development_profile_id
        )
        direct_projection_flags = tuple(
            development_policy.preserve_direct_projection
            and abs(value - source_projection_length)
            <= manufacturing_tolerance_mm
            for value in raw
        )
        targets = tuple(
            source_projection_length
            if direct
            else quantize_derived_flange_length(value, development_policy)
            if profile_authorized and valid
            else value
            for value, direct, valid in zip(
                raw,
                direct_projection_flags,
                valid_straight_strips,
            )
        )
        certificate = {
            "authorized": profile_authorized and all(valid_straight_strips),
            "certificate_kind": "profile_authorized_rigid_development",
            "raw_lengths_mm": list(raw),
            "quantized_lengths_mm": list(targets),
            "direct_projection_flags": list(direct_projection_flags),
            "valid_straight_strip_flags": list(valid_straight_strips),
            "strip_tolerance_mm": strip_tolerance,
            "candidate_count": len(raw),
            "match_count": len(raw),
            "policy": asdict(development_policy),
        }
        return traced(FlangeDevelopmentEstimate(
            mode="variable_height_two_paths",
            target_lengths=targets,
            raw_lengths=raw,
            source_projection_length=source_projection_length,
            details=tuple(details),
            certificate=certificate,
        ))

    # A constant-height member with two measurably different flange paths
    # (end bevel / stepped flange).  The web projection exposes both
    # developed lengths, so emit two targets instead of one identical pair.
    ordered_details = tuple(reversed(details))
    raw_lengths = tuple(
        _numeric_measurement(item, "raw_length") for item in ordered_details
    )
    if max(raw_lengths) - min(raw_lengths) > max(
        5.0,
        0.005 * nominal_length,
    ):
        strip_tolerance = max(
            float(manufacturing_tolerance_mm),
            0.02 * float(flange_thickness),
        )
        valid_straight_strips = tuple(
            bool(item.get("straight"))
            and item.get("method") == "straight_strip_projection"
            and item.get("observed_strip_thickness_mm") is not None
            and abs(
                _numeric_measurement(item, "observed_strip_thickness_mm")
                - float(flange_thickness)
            )
            <= strip_tolerance
            and _numeric_measurement(
                item,
                "rectangular_fill_ratio",
                default=0.0,
            )
            >= 0.98
            for item in ordered_details
        )
        profile_authorized = development_policy.authorizes_profile(
            development_profile_id
        )
        direct_projection_flags = tuple(
            development_policy.preserve_direct_projection
            and abs(value - source_projection_length)
            <= manufacturing_tolerance_mm
            for value in raw_lengths
        )
        targets = tuple(
            source_projection_length
            if direct
            else quantize_derived_flange_length(value, development_policy)
            if profile_authorized and valid
            else value
            for value, direct, valid in zip(
                raw_lengths,
                direct_projection_flags,
                valid_straight_strips,
            )
        )
        certificate = {
            "authorized": profile_authorized and all(valid_straight_strips),
            "certificate_kind": "constant_height_two_flange_paths",
            "raw_lengths_mm": list(raw_lengths),
            "quantized_lengths_mm": list(targets),
            "direct_projection_flags": list(direct_projection_flags),
            "valid_straight_strip_flags": list(valid_straight_strips),
            "strip_tolerance_mm": strip_tolerance,
            "source_projection_length_mm": source_projection_length,
            "policy": asdict(development_policy),
        }
        return traced(FlangeDevelopmentEstimate(
            mode="constant_height_two_flange_paths",
            target_lengths=targets,
            raw_lengths=raw_lengths,
            source_projection_length=source_projection_length,
            details=ordered_details,
            certificate=certificate,
        ))

    # A constant-height member whose web bbox exceeds H contains a crank/offset.
    # Select the boundary-path measurement nearest the table length.  Preserve
    # that observed development instead of rounding toward an offline manual
    # result; without an associated explicit dimension, the proof layer routes
    # this inferred length to engineering review.
    path_candidates = [
        value
        for item in details
        for value in _numeric_measurement_sequence(item, "path_lengths")
    ]
    selection = select_profile_authorized_cranked_candidate(
        path_candidates,
        nominal_length_mm=nominal_length,
        nominal_text=f"{nominal_length:g}",
        policy=development_policy,
        geometric_tolerance_mm=manufacturing_tolerance_mm,
    )
    profile_authorized = development_policy.authorizes_profile(
        development_profile_id
    )
    authorized = profile_authorized and selection.authorized
    best = (
        float(selection.selected_raw_length_mm)
        if selection.selected_raw_length_mm is not None
        else min(path_candidates, key=lambda value: abs(value - nominal_length))
    )
    target = (
        float(selection.quantized_length_mm)
        if authorized and selection.quantized_length_mm is not None
        else best
    )
    if abs(target - source_projection_length) <= manufacturing_tolerance_mm:
        target = source_projection_length
    certificate = {
        "authorized": authorized,
        "certificate_kind": "profile_authorized_cranked_development",
        "raw_lengths_mm": [best],
        "quantized_lengths_mm": [target],
        "candidate_lengths_mm": path_candidates,
        "candidate_count": selection.candidate_count,
        "match_count": selection.match_count,
        "binding_tolerance_mm": selection.tolerance_mm,
        "policy": asdict(development_policy),
    }
    return traced(FlangeDevelopmentEstimate(
        mode="constant_height_cranked_path",
        target_lengths=(target,),
        raw_lengths=(best,),
        source_projection_length=source_projection_length,
        details=tuple(details),
        certificate=certificate,
    ))


def extend_flange_polygon_to_length(
    polygon: Polygon,
    target_length: float,
    *,
    long_axis: str,
    tolerance: float = 1e-6,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> Polygon:
    """Extend or shorten a flange plan without distorting its diagonal end cut.

    A constant-height member can expose two flange paths of different lengths
    (end bevel / stepped web); the shorter flange is a real member property,
    so shortening is allowed as long as the resulting outline stays valid.
    """
    min_x, min_y, max_x, max_y = polygon.bounds
    current = (max_x - min_x) if long_axis == "x" else (max_y - min_y)
    delta = target_length - current
    if abs(delta) <= tolerance:
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_rigid_extension",
            status="not_applicable",
            title_zh="翼缘刚性延长",
            summary_zh="目标长度与投影长度一致，无需延长。",
            hypothesis_id=hypothesis_id,
            shapes=(polygon_shape("flange-no-extension", "face_selected", polygon),),
            payload={"current_length_mm": current, "target_length_mm": target_length},
        )
        return polygon

    coordinates = [(float(x), float(y)) for x, y in list(polygon.exterior.coords)[:-1]]
    diagonal_midpoints: list[float] = []
    for first, second in zip(coordinates, coordinates[1:] + coordinates[:1]):
        dx = second[0] - first[0]
        dy = second[1] - first[1]
        if abs(dx) > tolerance and abs(dy) > tolerance:
            diagonal_midpoints.append(
                ((first[0] + second[0]) / 2.0) if long_axis == "x"
                else ((first[1] + second[1]) / 2.0)
            )
    minimum = min_x if long_axis == "x" else min_y
    maximum = max_x if long_axis == "x" else max_y
    shaped_at_max = bool(diagonal_midpoints) and sum(diagonal_midpoints) / len(diagonal_midpoints) > (minimum + maximum) / 2.0
    shaped_at_min = bool(diagonal_midpoints) and not shaped_at_max

    result: list[tuple[float, float]] = []
    for x, y in coordinates:
        value = x if long_axis == "x" else y
        if shaped_at_max:
            move = value > minimum + tolerance
            offset = delta if move else 0.0
        elif shaped_at_min:
            move = value < maximum - tolerance
            offset = -delta if move else 0.0
        else:
            move = abs(value - maximum) <= tolerance
            offset = delta if move else 0.0
        result.append((x + offset, y) if long_axis == "x" else (x, y + offset))
    extended = Polygon(result)
    if not extended.is_valid or extended.area <= 0:
        raise ValueError("Flange extension produced an invalid polygon.")
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="flange_rigid_extension",
        status="observed",
        title_zh="翼缘刚性延长",
        summary_zh=f"沿 {long_axis} 轴延长 {delta:.3f} mm，保留异形端部。",
        hypothesis_id=hypothesis_id,
        shapes=(
            polygon_shape("flange-before-extension", "repair_removed", polygon),
            polygon_shape("flange-after-extension", "repair_added", extended),
        ),
        payload={
            "long_axis": long_axis,
            "current_length_mm": current,
            "target_length_mm": target_length,
            "delta_mm": delta,
            "shaped_at_min": shaped_at_min,
            "shaped_at_max": shaped_at_max,
        },
    )
    return extended

def source_arcs(entities: list[DXFEntity]) -> list[SourceArc]:
    """Collect exact Part arcs, including hidden arcs used as projection evidence.

    Some detailing exporters draw the visible manufacturing edge as a chain of
    short LINE chords while retaining the exact ARC on the hidden-line
    projection.  Hidden arcs are therefore preserved as *evidence* and are only
    accepted later when an entire selected boundary chain agrees with their
    radius, angular interval and endpoints.  They are never copied blindly.
    """
    result: list[SourceArc] = []
    signatures: set[tuple[int, int, int, int, int]] = set()
    for entity in entities:
        if (
            entity.dxf.layer != "Part"
            or entity.dxftype() != "ARC"
        ):
            continue
        signature = (
            round(float(entity.dxf.center.x) * 1000),
            round(float(entity.dxf.center.y) * 1000),
            round(float(entity.dxf.radius) * 1000),
            round(float(entity.dxf.start_angle) * 1000),
            round(float(entity.dxf.end_angle) * 1000),
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        result.append(
            SourceArc(
                center=Point2D(float(entity.dxf.center.x), float(entity.dxf.center.y)),
                radius=float(entity.dxf.radius),
                start_angle=float(entity.dxf.start_angle),
                end_angle=float(entity.dxf.end_angle),
                start=Point2D(float(entity.start_point.x), float(entity.start_point.y)),
                end=Point2D(float(entity.end_point.x), float(entity.end_point.y)),
            )
        )
    return result


def _circular_angle_distance(first: float, second: float) -> float:
    delta = abs((first - second) % 360.0)
    return min(delta, 360.0 - delta)


def _angle_in_arc(angle: float, arc: SourceArc, angular_tolerance: float = 2.0) -> bool:
    """Membership test robust at 0/360 and slightly noisy arc endpoints."""
    relative = (angle - arc.start_angle) % 360.0
    if relative <= arc.sweep + angular_tolerance:
        return True
    return (
        _circular_angle_distance(angle, arc.start_angle) <= angular_tolerance
        or _circular_angle_distance(angle, arc.end_angle) <= angular_tolerance
    )


def _point_on_arc(point: tuple[float, float], arc: SourceArc, tolerance: float) -> bool:
    radial = hypot(point[0] - arc.center.x, point[1] - arc.center.y)
    if abs(radial - arc.radius) > tolerance:
        return False
    angle = degrees(atan2(point[1] - arc.center.y, point[0] - arc.center.x)) % 360.0
    return _angle_in_arc(angle, arc)


def _simplify_collinear(
    vertices: list[BulgeVertex],
    *,
    transverse_tolerance: float = 0.01,
    machine_collinear_tolerance: float = 1e-9,
) -> list[BulgeVertex]:
    """Remove straight subdivisions and zero-width reversal hairs.

    DXF projection linework can contain two almost coincident visible/hidden
    edges.  Precision noding may then emit a long A-B-C excursion where B lies
    slightly beyond C but all three points represent the same physical edge.
    Angular tests are unstable for that hairpin, so a reversal may use the
    manufacturing-scale perpendicular tolerance.  Forward motion is different:
    only machine-collinear subdivisions are removed, preserving a real shallow
    kink regardless of its transverse size.
    """

    vertices = vertices[:]
    changed = True
    while changed and len(vertices) > 3:
        changed = False
        for index in range(len(vertices)):
            previous = vertices[index - 1]
            current = vertices[index]
            following = vertices[(index + 1) % len(vertices)]
            if abs(previous.bulge) > 1e-12 or abs(current.bulge) > 1e-12:
                continue
            ax = current.x - previous.x
            ay = current.y - previous.y
            outgoing_x = following.x - current.x
            outgoing_y = following.y - current.y
            baseline_x = following.x - previous.x
            baseline_y = following.y - previous.y
            baseline = hypot(baseline_x, baseline_y)
            deviation = (
                abs(ax * baseline_y - ay * baseline_x) / baseline
                if baseline > 1e-12
                else 0.0
            )
            reversal = ax * outgoing_x + ay * outgoing_y < 0.0
            allowed_deviation = (
                transverse_tolerance
                if reversal
                else machine_collinear_tolerance
            )
            if deviation <= allowed_deviation:
                vertices.pop(index)
                changed = True
                break
    return vertices


def _segment_arc_candidate(
    first: tuple[float, float],
    second: tuple[float, float],
    arc: SourceArc,
    *,
    radial_tolerance: float,
    maximum_chord_sagitta: float,
) -> bool:
    """Return true when a boundary chord is credible evidence for ``arc``.

    Exact ARC polygonization places the segment midpoint on the circle.  A
    chorded CAD approximation does not, so endpoint radial agreement is used
    together with a strict sagitta limit.  Final acceptance still requires the
    complete contiguous chain to match the source ARC endpoints.
    """
    if not (
        _point_on_arc(first, arc, radial_tolerance)
        and _point_on_arc(second, arc, radial_tolerance)
    ):
        return False
    midpoint = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
    radial = hypot(midpoint[0] - arc.center.x, midpoint[1] - arc.center.y)
    sagitta = abs(arc.radius - radial)
    return sagitta <= maximum_chord_sagitta


def _arc_chain_matches_endpoints(
    first: Point2D,
    second: Point2D,
    arc: SourceArc,
    *,
    endpoint_tolerance: float,
) -> tuple[bool, bool]:
    forward_error = first.distance_to(arc.start) + second.distance_to(arc.end)
    reverse_error = first.distance_to(arc.end) + second.distance_to(arc.start)
    limit = 2.0 * endpoint_tolerance
    if min(forward_error, reverse_error) > limit:
        return False, False
    return True, forward_error <= reverse_error


def ring_to_bulge_contour(
    ring,
    arcs: list[SourceArc],
    *,
    tolerance: float,
) -> BulgeContour:
    coordinates = [(float(x), float(y)) for x, y in list(ring.coords)[:-1]]
    if len(coordinates) < 3:
        raise ValueError("Polygon ring has fewer than three vertices.")

    # Manufacturing fillets are sometimes exported as 3-8 straight chords.
    # One millimetre is large enough to recognise those
    # chords but too small to turn a deliberate chamfer into a typical radius.
    maximum_chord_sagitta = max(1.0, tolerance * 8.0)
    # Chorded visible edges can deviate radially by around 0.1 mm from the
    # exact hidden ARC even when their endpoints originate from the same CAD
    # curve.  Endpoint matching of the *whole chain* remains the final guard.
    radial_tolerance = max(0.20, tolerance * 10.0)
    assignments: list[int | None] = []
    for index, first in enumerate(coordinates):
        second = coordinates[(index + 1) % len(coordinates)]
        matches = [
            arc_index
            for arc_index, arc in enumerate(arcs)
            if _segment_arc_candidate(
                first,
                second,
                arc,
                radial_tolerance=radial_tolerance,
                maximum_chord_sagitta=maximum_chord_sagitta,
            )
        ]
        assignments.append(matches[0] if len(matches) == 1 else None)

    # Start on an ordinary line whenever possible so an arc group never wraps
    # across the list boundary.
    if any(assignment is None for assignment in assignments):
        rotate = next(index for index, assignment in enumerate(assignments) if assignment is None)
        coordinates = coordinates[rotate:] + coordinates[:rotate]
        assignments = assignments[rotate:] + assignments[:rotate]

    vertices: list[BulgeVertex] = []
    index = 0
    while index < len(coordinates):
        arc_index = assignments[index]
        if arc_index is None:
            x, y = coordinates[index]
            vertices.append(BulgeVertex(x, y, 0.0))
            index += 1
            continue

        end_index = index + 1
        while end_index < len(coordinates) and assignments[end_index] == arc_index:
            end_index += 1
        arc = arcs[arc_index]
        first = Point2D(*coordinates[index])
        second = Point2D(*coordinates[end_index % len(coordinates)])
        accepted, forward = _arc_chain_matches_endpoints(
            first,
            second,
            arc,
            endpoint_tolerance=max(0.10, tolerance * 5.0),
        )
        if not accepted:
            # The endpoints happen to lie on the same circle but the chain does
            # not represent the complete source arc.  Preserve the original
            # straight segments rather than inventing curvature.
            for cursor in range(index, end_index):
                x, y = coordinates[cursor]
                vertices.append(BulgeVertex(x, y, 0.0))
            index = end_index
            continue

        magnitude = tan(radians(arc.sweep) / 4.0)
        if forward:
            vertices.append(BulgeVertex(arc.start.x, arc.start.y, magnitude))
        else:
            vertices.append(BulgeVertex(arc.end.x, arc.end.y, -magnitude))
        index = end_index

    return BulgeContour(_simplify_collinear(vertices), closed=True)


def polygon_to_bulge_contours(
    polygon: Polygon,
    arcs: list[SourceArc],
    *,
    grid_size: float,
) -> tuple[BulgeContour, list[BulgeContour]]:
    tolerance = max(0.02, grid_size * 3.0)
    outer = ring_to_bulge_contour(polygon.exterior, arcs, tolerance=tolerance)
    inner = [
        ring_to_bulge_contour(ring, arcs, tolerance=tolerance)
        for ring in polygon.interiors
    ]
    return outer, inner


def translate_contour(contour: BulgeContour, dx: float, dy: float) -> BulgeContour:
    return contour.translated(dx, dy)

# ---------------------------------------------------------------------------
# Topology conservation helpers
# ---------------------------------------------------------------------------

def _longitudinal_bounds(polygon: Polygon, long_axis: str) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = polygon.bounds
    return (min_x, max_x) if long_axis == "x" else (min_y, max_y)


def _transverse_bounds(polygon: Polygon, long_axis: str) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = polygon.bounds
    return (min_y, max_y) if long_axis == "x" else (min_x, max_x)


def _interval_overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _polygon_nearly_equal(
    first: Polygon,
    second: Polygon,
    *,
    distance_tolerance: float = 0.02,
    area_ratio_tolerance: float = 1e-4,
) -> bool:
    scale = max(first.area, second.area, 1.0)
    return (
        first.hausdorff_distance(second) <= distance_tolerance
        and first.symmetric_difference(second).area / scale <= area_ratio_tolerance
    )


def _clean_candidate_polygon(
    polygon: Polygon,
    *,
    grid_size: float,
    projection_semantics: ProjectionBoundarySemantics | None = None,
    repair_diagnostics: list[dict[str, object]] | None = None,
    fidelity_tolerance_mm: float = 1e-7,
) -> Polygon:
    """Remove precision-node and collinear noise without changing real features.

    DXF projections frequently contain duplicated visible/hidden edges with tiny
    endpoint offsets.  Polygonization can therefore leave 0.001–0.01 mm notches
    in an otherwise straight plate boundary.  A conservative simplify pass is
    applied only after a valid physical candidate has been assembled.
    """
    tolerance = max(0.0015, grid_size * 2.5)
    cleaned = polygon.simplify(tolerance, preserve_topology=True)
    if isinstance(cleaned, Polygon) and cleaned.is_valid and cleaned.area > 0:
        if projection_semantics is not None:
            decision = evaluate_boundary_repair(
                polygon,
                cleaned,
                projection_semantics,
                fidelity_tolerance_mm=fidelity_tolerance_mm,
                repair_kind="candidate_polygon_simplification",
            )
            if repair_diagnostics is not None:
                repair_diagnostics.append(decision.to_dict())
            return decision.polygon
        return cleaned
    return polygon


def _longitudinal_rail_lengths(
    polygon: Polygon,
    *,
    long_axis: str,
    tolerance_mm: float,
) -> tuple[float, float] | None:
    """Return the two long-axis rails of one simple convex flange plate."""

    coordinates = list(polygon.exterior.coords)
    if coordinates and coordinates[0] == coordinates[-1]:
        coordinates.pop()
    # Polygonisation may node one physical end course at hidden-line
    # intersections.  Merge only consecutive, manufacturing-collinear pieces;
    # this changes no boundary and prevents the node count from masquerading as
    # a more complex plate shape.
    changed = True
    while changed and len(coordinates) > 3:
        changed = False
        for index, current in enumerate(coordinates):
            previous = coordinates[index - 1]
            following = coordinates[(index + 1) % len(coordinates)]
            course = LineString((previous, following))
            if (
                course.length > tolerance_mm
                and Point(current).distance(course) <= tolerance_mm
                and (
                    (current[0] - previous[0]) * (following[0] - current[0])
                    + (current[1] - previous[1]) * (following[1] - current[1])
                )
                >= 0.0
            ):
                del coordinates[index]
                changed = True
                break
    if not 4 <= len(coordinates) <= 6 or polygon.interiors:
        return None
    hull_area_delta = float(polygon.convex_hull.area - polygon.area)
    if hull_area_delta > max(1e-6, tolerance_mm * tolerance_mm):
        return None

    rails: list[float] = []
    end_edges = 0
    for start, end in zip(coordinates, coordinates[1:] + coordinates[:1]):
        dx = float(end[0] - start[0])
        dy = float(end[1] - start[1])
        longitudinal = dx if long_axis == "x" else dy
        transverse = dy if long_axis == "x" else dx
        if abs(transverse) <= tolerance_mm and abs(longitudinal) > tolerance_mm:
            rails.append(abs(longitudinal))
        elif hypot(longitudinal, transverse) > tolerance_mm:
            end_edges += 1

    if len(rails) != 2 or not 2 <= end_edges <= 4:
        return None
    return tuple(sorted(rails))


def _full_transverse_end_edges(
    polygon: Polygon,
    *,
    long_axis: str,
    flange_width: float,
    tolerance_mm: float,
) -> tuple[LineString, ...]:
    """Return source edges that close the complete flange width."""

    if _longitudinal_rail_lengths(
        polygon,
        long_axis=long_axis,
        tolerance_mm=tolerance_mm,
    ) is None:
        return ()
    coordinates = list(polygon.exterior.coords)
    if coordinates and coordinates[0] == coordinates[-1]:
        coordinates.pop()
    caps: list[LineString] = []
    orientation_tolerance = max(
        2.0 * tolerance_mm,
        0.001 * flange_width,
    )
    for start, end in zip(coordinates, coordinates[1:] + coordinates[:1]):
        dx = float(end[0] - start[0])
        dy = float(end[1] - start[1])
        longitudinal = dx if long_axis == "x" else dy
        transverse = dy if long_axis == "x" else dx
        edge = LineString((start, end))
        if (
            abs(longitudinal) <= orientation_tolerance
            and abs(abs(transverse) - flange_width) <= tolerance_mm
        ):
            caps.append(edge)
    return tuple(caps)


def _recover_source_backed_nested_flange_pair(
    *,
    primary: Polygon,
    seeds: Iterable[Polygon],
    entities: list[DXFEntity],
    entity_source_ids: tuple[str, ...],
    long_axis: str,
    flange_width: float,
    main_flange_spans: dict[str, float],
    grid_size: float,
    manufacturing_tolerance_mm: float,
    faces: Iterable[Polygon] = (),
    nominal_length: float | None = None,
) -> tuple[Polygon, Polygon] | None:
    """Recover two nested physical flanges only from complete source evidence."""

    # Stay on the finest grid that still preserves short source chamfers.  A
    # full grid cell is the maximum quantisation drift between polygonisation
    # and the original source line; using half a cell can reject that same
    # source edge and force a coarser retry which then erases the chamfer.
    boundary_tolerance = max(1e-7, grid_size * 1.02)

    def cleaned_with_contract(
        candidate: Polygon,
    ) -> tuple[Polygon, tuple[float, float], tuple[LineString, ...]] | None:
        semantics = analyse_projection_boundary(
            candidate,
            entities,
            entity_source_ids=entity_source_ids,
            association_tolerance_mm=boundary_tolerance,
        )
        cleaned = _clean_candidate_polygon(
            candidate,
            grid_size=grid_size,
            projection_semantics=semantics,
            fidelity_tolerance_mm=boundary_tolerance,
        )
        rails = _longitudinal_rail_lengths(
            cleaned,
            long_axis=long_axis,
            tolerance_mm=manufacturing_tolerance_mm,
        )
        caps = _full_transverse_end_edges(
            cleaned,
            long_axis=long_axis,
            flange_width=flange_width,
            tolerance_mm=manufacturing_tolerance_mm,
        )
        _, transverse = _axis_lengths(cleaned.bounds, long_axis)
        if (
            rails is None
            or not caps
            or abs(transverse - flange_width) > manufacturing_tolerance_mm
        ):
            return None
        conservation = assess_selected_projection_boundary(
            cleaned,
            entities,
            entity_source_ids=entity_source_ids,
            association_tolerance_mm=boundary_tolerance,
            fidelity_tolerance_mm=boundary_tolerance,
        )
        if (
            not conservation.applied
            or conservation.lost_source_ids
            or not conservation.protected_source_ids
        ):
            return None
        return cleaned, rails, caps

    seed_rows = tuple(seeds)
    pair_inputs: list[tuple[Polygon, Polygon]] = [
        (primary, seed) for seed in seed_rows
    ]
    face_rows = list(faces)
    if nominal_length is not None and face_rows:
        for seed in seed_rows:
            connected_candidates: list[Polygon] = []
            connected, _ = _complete_connected_projection(
                seed,
                face_rows,
                long_axis=long_axis,
                flange_width=flange_width,
                nominal_length=nominal_length,
                grid_size=grid_size,
                completed_candidates=connected_candidates,
            )
            pair_inputs.extend(
                (candidate, seed) for candidate in connected_candidates
            )
            if not connected_candidates:
                pair_inputs.append((connected, seed))

    geometric_candidates: list[
        tuple[Polygon, tuple[float, float], Polygon, tuple[float, float]]
    ] = []
    for outer, inner in pair_inputs:
        outer_contract = cleaned_with_contract(outer)
        inner_contract = cleaned_with_contract(inner)
        if outer_contract is None or inner_contract is None:
            continue
        cleaned_outer, outer_rails, _outer_caps = outer_contract
        cleaned_inner, inner_rails, _inner_caps = inner_contract
        if _polygon_nearly_equal(cleaned_inner, cleaned_outer):
            continue
        if not cleaned_outer.buffer(manufacturing_tolerance_mm).covers(cleaned_inner):
            continue
        minimum_difference_area = manufacturing_tolerance_mm * flange_width
        if cleaned_outer.symmetric_difference(cleaned_inner).area <= minimum_difference_area:
            continue
        if any(
            _polygon_nearly_equal(cleaned_outer, existing[0])
            and _polygon_nearly_equal(cleaned_inner, existing[2])
            for existing in geometric_candidates
        ):
            continue
        geometric_candidates.append(
            (cleaned_outer, outer_rails, cleaned_inner, inner_rails)
        )

    if not geometric_candidates:
        return None
    if len(geometric_candidates) != 1:
        raise ValueError("Nested flange projection has multiple physical seed candidates.")

    cleaned_primary, primary_rails, cleaned_seed, seed_rails = geometric_candidates[0]

    def rail_matches(rails: tuple[float, float], span: float) -> bool:
        return any(
            abs(value - float(span)) <= manufacturing_tolerance_mm
            for value in rails
        )

    high_span = main_flange_spans.get("high")
    low_span = main_flange_spans.get("low")
    if high_span is not None and low_span is not None:
        if abs(float(high_span) - float(low_span)) <= manufacturing_tolerance_mm:
            unique_side_evidence = rail_matches(
                primary_rails,
                float(high_span),
            ) and rail_matches(seed_rails, float(low_span))
        else:
            assignments = [
                (upper, lower)
                for upper, lower in (
                    (primary_rails, seed_rails),
                    (seed_rails, primary_rails),
                )
                if rail_matches(upper, high_span) and rail_matches(lower, low_span)
            ]
            unique_side_evidence = len(assignments) == 1
    else:
        observed_spans = tuple(float(span) for span in main_flange_spans.values())
        candidate_matches = tuple(
            any(rail_matches(rails, span) for span in observed_spans)
            for rails in (primary_rails, seed_rails)
        )
        unique_side_evidence = sum(candidate_matches) == 1

    if not unique_side_evidence:
        return None
    return cleaned_primary, cleaned_seed


def _expand_at_longitudinal_ends(
    seed: Polygon,
    faces: list[Polygon],
    *,
    long_axis: str,
    minimum_transverse: float,
    maximum_transverse: float,
    grid_size: float,
) -> tuple[Polygon, list[int]]:
    """Grow a plate through adjacent end faces, never through internal strips.

    This addresses drawings where an end cap is split into a separate planar
    face by another projected edge.  Candidates are admitted only when they
    extend the current longitudinal envelope and overlap a meaningful portion
    of the plate width.  This is a topological rule, not a filename/sample rule.
    """
    current = seed
    used: list[int] = []
    tolerance = max(0.01, grid_size * 4.0)
    changed = True
    while changed:
        changed = False
        current_long = _longitudinal_bounds(current, long_axis)
        current_transverse = _transverse_bounds(current, long_axis)
        current_transverse_size = current_transverse[1] - current_transverse[0]
        for index, face in sorted(enumerate(faces), key=lambda item: item[1].area, reverse=True):
            if index in used:
                continue
            # Ignore faces already contained in the current plate.
            if face.difference(current.buffer(tolerance)).area <= max(1e-4, grid_size * grid_size):
                continue
            face_length, face_transverse_size = _axis_lengths(face.bounds, long_axis)
            if face_transverse_size < minimum_transverse:
                continue
            face_long = _longitudinal_bounds(face, long_axis)
            extends_min = face_long[0] < current_long[0] - tolerance
            extends_max = face_long[1] > current_long[1] + tolerance
            if not (extends_min or extends_max):
                continue
            overlap = _interval_overlap(current_transverse, _transverse_bounds(face, long_axis))
            if overlap < max(0.15 * current_transverse_size, 0.15 * face_transverse_size):
                continue
            if current.distance(face) > tolerance and not current.buffer(tolerance).intersects(face):
                continue
            merged = unary_union([current, face])
            if isinstance(merged, MultiPolygon):
                merged = unary_union([part.buffer(tolerance) for part in merged.geoms]).buffer(-tolerance)
            if not isinstance(merged, Polygon) or not merged.is_valid:
                continue
            _, merged_transverse = _axis_lengths(merged.bounds, long_axis)
            if merged_transverse > maximum_transverse:
                continue
            current = merged
            used.append(index)
            changed = True
            break
    # Defer simplification until the selected boundary has source-edge
    # semantics.  Cleaning here would erase evidence before the final guard can
    # associate it.
    return current, used



def _polygonize_part_entities_with_hidden(
    entities: list[DXFEntity], grid_size: float
) -> list[Polygon]:
    """Polygonize Part linework including hidden segments for boundary bridges.

    Hidden lines are never used wholesale as a plate contour.  They are exposed
    only so `_merge_boundary_bridge_faces` can recover a small face which closes
    a gap on the outside edge of an otherwise established web.
    """
    linework: list[LineString] = []
    for entity in entities:
        if entity.dxf.layer != "Part" or entity.dxftype() not in {"LINE", "ARC"}:
            continue
        if entity.dxftype() == "LINE":
            start = entity.dxf.start
            end = entity.dxf.end
            if hypot(float(end.x - start.x), float(end.y - start.y)) <= 0.05:
                continue
            linework.append(
                LineString(
                    [
                        (float(start.x), float(start.y)),
                        (float(end.x), float(end.y)),
                    ]
                )
            )
        else:
            linework.append(LineString(arc_points(entity)))
    if not linework:
        return []
    noded = unary_union(
        set_precision(MultiLineString(linework), grid_size, mode="valid_output")
    )
    return list(polygonize(noded))


def _merge_boundary_bridge_faces(
    polygon: Polygon,
    faces: list[Polygon],
    *,
    long_axis: str,
    profile_height: float,
    nominal_length: float,
    grid_size: float,
) -> Polygon:
    """Merge small hidden-line faces that close only an exterior edge gap."""
    current = polygon
    tolerance = max(0.01, grid_size * 4.0)
    changed = True
    while changed:
        changed = False
        current_transverse = _transverse_bounds(current, long_axis)
        for face in sorted(faces, key=lambda item: item.area):
            if face.difference(current.buffer(tolerance)).area <= max(1e-4, grid_size * grid_size):
                continue
            rectangle_area = (face.bounds[2] - face.bounds[0]) * (face.bounds[3] - face.bounds[1])
            if rectangle_area <= 0 or face.area / rectangle_area < 0.95:
                continue
            if len(face.exterior.coords) > 6 or len(face.interiors) > 0:
                continue
            length, transverse = _axis_lengths(face.bounds, long_axis)
            if length > max(0.10 * nominal_length, 150.0):
                continue
            if transverse > max(0.10 * profile_height, 50.0):
                continue
            face_transverse = _transverse_bounds(face, long_axis)
            touches_outer_transverse = (
                abs(face_transverse[0] - current_transverse[0]) <= tolerance
                or abs(face_transverse[1] - current_transverse[1]) <= tolerance
            )
            if not touches_outer_transverse:
                continue
            if not current.buffer(tolerance).intersects(face):
                continue
            merged = unary_union([current, face])
            if not isinstance(merged, Polygon) or not merged.is_valid:
                continue
            # A bridge may add area but must not change the established bbox.
            differences = [
                abs(merged.bounds[index] - current.bounds[index])
                for index in range(4)
            ]
            if max(differences) > tolerance:
                continue
            current = merged
            changed = True
            break
    return current


def _complete_connected_projection(
    polygon: Polygon,
    faces: list[Polygon],
    *,
    long_axis: str,
    flange_width: float,
    nominal_length: float,
    grid_size: float,
    completed_candidates: list[Polygon] | None = None,
) -> tuple[Polygon, list[int]]:
    """Complete an overlapping flange projection from its connected face graph."""
    current = polygon
    used: list[int] = []
    tolerance = max(0.01, grid_size * 4.0)
    initial_transverse = _axis_lengths(polygon.bounds, long_axis)[1]
    maximum_transverse = initial_transverse + max(0.5, 0.001 * flange_width)
    changed = True
    while changed:
        changed = False
        for index, face in sorted(enumerate(faces), key=lambda item: item[1].area, reverse=True):
            if index in used:
                continue
            if face.difference(current.buffer(tolerance)).area <= max(1e-4, grid_size * grid_size):
                continue
            if not current.buffer(tolerance).intersects(face):
                continue
            merged = unary_union([current, face])
            if isinstance(merged, MultiPolygon):
                continue
            if not isinstance(merged, Polygon) or not merged.is_valid:
                continue
            length, transverse = _axis_lengths(merged.bounds, long_axis)
            if transverse > min(1.08 * flange_width, maximum_transverse):
                continue
            if length > max(1.25 * nominal_length, nominal_length + 500.0):
                continue
            current = merged
            used.append(index)
            if completed_candidates is not None:
                completed_candidates.append(current)
            changed = True
            break
    return current, used


def _complete_flange_seed_end_caps(
    seed: Polygon,
    faces: list[Polygon],
    *,
    long_axis: str,
    flange_width: float,
    nominal_length: float,
    grid_size: float,
) -> Polygon:
    """Attach only narrow full-width faces at a seed's longitudinal ends."""

    current = seed
    tolerance = max(0.01, grid_size * 4.0)
    maximum_cap_length = max(0.05 * nominal_length, 0.25 * flange_width, 100.0)
    changed = True
    while changed:
        changed = False
        current_long = _longitudinal_bounds(current, long_axis)
        current_transverse = _transverse_bounds(current, long_axis)
        for face in sorted(faces, key=lambda item: item.area):
            added = face.difference(current.buffer(tolerance))
            if added.area <= max(1e-4, grid_size * grid_size):
                continue
            face_length, face_width = _axis_lengths(face.bounds, long_axis)
            if face_length > maximum_cap_length or face_width < 0.80 * flange_width:
                continue
            face_long = _longitudinal_bounds(face, long_axis)
            touches_end = (
                abs(face_long[1] - current_long[0]) <= tolerance
                or abs(face_long[0] - current_long[1]) <= tolerance
            )
            if not touches_end:
                continue
            if _interval_overlap(
                current_transverse,
                _transverse_bounds(face, long_axis),
            ) < 0.80 * flange_width:
                continue
            merged = unary_union([current, face])
            if not isinstance(merged, Polygon) or not merged.is_valid:
                continue
            current = merged
            changed = True
            break
    return current

def _regularize_micro_topology(
    polygon: Polygon,
    *,
    epsilon: float = 0.25,
    maximum_relative_area_change: float = 1.0e-4,
    maximum_bbox_change: float = 0.02,
    projection_semantics: ProjectionBoundarySemantics | None = None,
    fidelity_tolerance_mm: float = 1e-7,
    long_axis: str | None = None,
) -> tuple[Polygon, dict[str, object]]:
    """Remove sub-millimetre slivers and close sub-millimetre boundary cracks.

    Duplicate visible/hidden projection lines can leave a long zero-width spike
    or a 0.1-0.3 mm strip in an otherwise exact plate.  A mitred morphological
    opening/closing is accepted only when the material-area and bounding-box
    changes are negligible, so real notches and fabrication features survive.
    """
    candidate = (
        polygon.buffer(-epsilon, join_style=2)
        .buffer(2.0 * epsilon, join_style=2)
        .buffer(-epsilon, join_style=2)
    )
    if not isinstance(candidate, Polygon) or not candidate.is_valid or candidate.area <= 0:
        return polygon, {"applied": False, "reason": "invalid_candidate"}
    relative = abs(candidate.area - polygon.area) / max(polygon.area, 1.0)
    bbox_change = max(abs(candidate.bounds[i] - polygon.bounds[i]) for i in range(4))
    if relative > maximum_relative_area_change or bbox_change > maximum_bbox_change:
        return polygon, {
            "applied": False,
            "reason": "change_exceeds_guard",
            "relative_area_change": relative,
            "bbox_change_mm": bbox_change,
        }
    if projection_semantics is not None:
        decision = (
            evaluate_longitudinal_projection_overlay_repair(
                polygon,
                candidate,
                projection_semantics,
                long_axis=long_axis,
                maximum_separation=2.0 * epsilon,
                fidelity_tolerance_mm=fidelity_tolerance_mm,
            )
            if long_axis in {"x", "y"}
            else evaluate_boundary_repair(
                polygon,
                candidate,
                projection_semantics,
                fidelity_tolerance_mm=fidelity_tolerance_mm,
                repair_kind="micro_topology_regularization",
            )
        )
        return decision.polygon, {
            **decision.to_dict(),
            "epsilon_mm": epsilon,
            "relative_area_change": relative,
            "bbox_change_mm": bbox_change,
        }
    return candidate, {
        "applied": True,
        "epsilon_mm": epsilon,
        "relative_area_change": relative,
        "bbox_change_mm": bbox_change,
    }


def _complete_parallel_terminal_web_strip(
    polygon: Polygon,
    faces: list[Polygon],
    *,
    source_entities: list[DXFEntity],
    entity_source_ids: tuple[str, ...] = (),
    long_axis: str,
    web_thickness: float,
    grid_size: float,
) -> tuple[Polygon, int | None, dict[str, object]]:
    """Merge one source-backed terminal strip whose connectors equal tw."""

    if long_axis not in {"x", "y"}:
        raise ValueError("long_axis must be 'x' or 'y'")
    if web_thickness <= 0.0:
        return polygon, None, {"applied": False, "reason": "invalid_web_thickness"}

    manufacturing_tolerance = max(0.15, grid_size * 5.0)
    fidelity_tolerance = max(1e-7, grid_size * 0.51)
    current_long = _longitudinal_bounds(polygon, long_axis)
    source_lines = [
        LineString(
            (
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            )
        )
        for entity in solid_part_entities(source_entities)
        if entity.dxftype() == "LINE"
    ]
    if not source_lines:
        return polygon, None, {"applied": False, "reason": "no_direct_source_lines"}
    source_band = unary_union(source_lines).buffer(
        fidelity_tolerance,
        cap_style=2,
        join_style=2,
    )

    qualified: list[tuple[int, Polygon, Polygon, dict[str, object]]] = []
    for index, face in enumerate(faces):
        if not isinstance(face, Polygon) or not face.is_valid or face.interiors:
            continue
        coordinates = [
            (float(x), float(y)) for x, y in list(face.exterior.coords)[:-1]
        ]
        if len(coordinates) != 4:
            continue
        segments: list[dict[str, object]] = []
        for start, end in zip(coordinates, coordinates[1:] + coordinates[:1]):
            dx = end[0] - start[0]
            dy = end[1] - start[1]
            length = hypot(dx, dy)
            if length <= fidelity_tolerance:
                break
            longitudinal = dx if long_axis == "x" else dy
            transverse = dy if long_axis == "x" else dx
            segments.append(
                {
                    "line": LineString((start, end)),
                    "length": length,
                    "longitudinal": longitudinal,
                    "transverse": transverse,
                    "angle": _segment_angle_mod_180(start, end),
                }
            )
        if len(segments) != 4:
            continue
        connectors = [
            segment
            for segment in segments
            if abs(float(segment["transverse"])) <= manufacturing_tolerance
        ]
        rails = [segment for segment in segments if segment not in connectors]
        if len(connectors) != 2 or len(rails) != 2:
            continue
        if any(
            abs(float(segment["length"]) - web_thickness)
            > manufacturing_tolerance
            for segment in connectors
        ):
            continue
        if min(float(segment["length"]) for segment in rails) <= (
            web_thickness + manufacturing_tolerance
        ):
            continue
        if (
            abs(float(rails[0]["length"]) - float(rails[1]["length"]))
            > manufacturing_tolerance
            or _angle_distance_180(
                float(rails[0]["angle"]), float(rails[1]["angle"])
            )
            > 0.1
        ):
            continue

        shared_rails = [
            segment
            for segment in rails
            if float(segment["line"].intersection(polygon.boundary).length)
            >= float(segment["length"]) - fidelity_tolerance
        ]
        if len(shared_rails) != 1:
            continue
        shared = shared_rails[0]
        outer = rails[0] if rails[1] is shared else rails[1]

        def midpoint_long(segment: dict[str, object]) -> float:
            midpoint = segment["line"].interpolate(0.5, normalized=True)
            return float(midpoint.x if long_axis == "x" else midpoint.y)

        outer_coordinates = list(outer["line"].coords)
        outer_values = [
            float(point[0] if long_axis == "x" else point[1])
            for point in outer_coordinates
        ]
        shared_mid = midpoint_long(shared)
        outer_mid = midpoint_long(outer)
        at_high_end = (
            abs(max(outer_values) - current_long[1]) <= manufacturing_tolerance
            and outer_mid > shared_mid + 0.5 * web_thickness
        )
        at_low_end = (
            abs(min(outer_values) - current_long[0]) <= manufacturing_tolerance
            and outer_mid < shared_mid - 0.5 * web_thickness
        )
        if not (at_high_end or at_low_end):
            continue

        if any(
            segment["line"].difference(source_band).length > fidelity_tolerance
            for segment in segments
        ):
            continue
        if face.difference(polygon).area <= max(0.5, grid_size * grid_size * 10.0):
            continue
        merged = unary_union((polygon, face))
        if not isinstance(merged, Polygon) or not merged.is_valid:
            continue
        if max(
            abs(merged.bounds[dimension] - polygon.bounds[dimension])
            for dimension in range(4)
        ) > manufacturing_tolerance:
            continue

        current_semantics = analyse_projection_boundary(
            polygon,
            source_entities,
            entity_source_ids=entity_source_ids,
            association_tolerance_mm=fidelity_tolerance,
        )
        merged_boundary_band = merged.boundary.buffer(
            fidelity_tolerance,
            cap_style=2,
            join_style=2,
        )
        shared_interface_band = face.boundary.intersection(polygon.boundary).buffer(
            fidelity_tolerance,
            cap_style=2,
            join_style=2,
        )
        removed_direct_edges = [
            edge
            for edge in current_semantics.direct_edges
            if edge.line.difference(merged_boundary_band).length > fidelity_tolerance
        ]
        if any(
            edge.line.difference(shared_interface_band).length > fidelity_tolerance
            for edge in removed_direct_edges
        ):
            continue

        qualified.append(
            (
                index,
                face,
                merged,
                {
                    "source_ids": list(
                        analyse_projection_boundary(
                            face,
                            source_entities,
                            entity_source_ids=entity_source_ids,
                            association_tolerance_mm=fidelity_tolerance,
                        ).protected_source_ids
                    ),
                    "connector_lengths_mm": [
                        float(segment["length"]) for segment in connectors
                    ],
                    "rail_lengths_mm": [
                        float(segment["length"]) for segment in rails
                    ],
                    "removed_shared_source_edge_count": len(removed_direct_edges),
                },
            )
        )

    unique: list[tuple[int, Polygon, Polygon, dict[str, object]]] = []
    for candidate in qualified:
        if any(_polygon_nearly_equal(candidate[1], item[1]) for item in unique):
            continue
        unique.append(candidate)
    if len(unique) != 1:
        return polygon, None, {
            "applied": False,
            "reason": (
                "no_qualified_terminal_strip"
                if not unique
                else "ambiguous_terminal_strips"
            ),
            "qualified_face_indices": [item[0] for item in unique],
        }

    index, _, merged, details = unique[0]
    return merged, index, {
        "applied": True,
        "reason": "unique_source_backed_parallel_terminal_strip",
        "face_index": index,
        **details,
    }


def _complete_longitudinal_boundary_faces(
    polygon: Polygon,
    faces: list[Polygon],
    *,
    source_entities: list[DXFEntity] | None = None,
    entity_source_ids: tuple[str, ...] = (),
    long_axis: str,
    profile_height: float,
    nominal_length: float,
    web_thickness: float | None = None,
    grid_size: float,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> tuple[Polygon, list[int], dict[str, object]]:
    """Merge tall, narrow end faces split from the physical web projection.

    A projected flange edge can partition a rounded web end into a large body
    and one narrow side face.  Unlike a generic union, this pass only accepts a
    face located at a longitudinal extremity, spanning most of the web depth,
    and producing a single valid polygon with a stable transverse envelope.
    Solid and hidden-line polygonizations may both contribute evidence.
    """
    current = polygon
    used: list[int] = []
    tolerance = max(0.02, grid_size * 5.0)
    changed = True
    while changed:
        changed = False
        current_long = _longitudinal_bounds(current, long_axis)
        current_transverse = _transverse_bounds(current, long_axis)
        current_length, current_depth = _axis_lengths(current.bounds, long_axis)
        for index, face in sorted(enumerate(faces), key=lambda item: item[1].area, reverse=True):
            if index in used:
                continue
            added_area = face.difference(current.buffer(tolerance)).area
            if added_area <= max(0.5, grid_size * grid_size * 10.0):
                continue
            face_length, face_depth = _axis_lengths(face.bounds, long_axis)
            if face_depth < 0.65 * current_depth:
                continue
            if face_length > max(0.12 * profile_height, 0.04 * nominal_length, 250.0):
                continue
            face_long = _longitudinal_bounds(face, long_axis)
            near_low = face_long[1] <= current_long[0] + max(face_length, 0.08 * profile_height) + tolerance
            near_high = face_long[0] >= current_long[1] - max(face_length, 0.08 * profile_height) - tolerance
            if not (near_low or near_high):
                continue
            transverse_overlap = _interval_overlap(
                current_transverse, _transverse_bounds(face, long_axis)
            )
            if transverse_overlap < 0.60 * min(current_depth, face_depth):
                continue
            if current.distance(face) > tolerance and not current.buffer(tolerance).intersects(face):
                continue
            merged = unary_union([current, face])
            if isinstance(merged, MultiPolygon):
                merged = unary_union(
                    [part.buffer(tolerance, join_style=2) for part in merged.geoms]
                ).buffer(-tolerance, join_style=2)
            if not isinstance(merged, Polygon) or not merged.is_valid:
                continue
            merged_length, merged_depth = _axis_lengths(merged.bounds, long_axis)
            if merged_depth > current_depth + max(0.5, 0.002 * profile_height):
                continue
            if merged_length > max(nominal_length + 0.05 * profile_height, 1.02 * nominal_length):
                continue
            current = merged
            used.append(index)
            changed = True
            break

    parallel_completion: dict[str, object] = {
        "applied": False,
        "reason": "web_thickness_not_provided",
    }
    if web_thickness is not None:
        current, strip_index, parallel_completion = (
            _complete_parallel_terminal_web_strip(
                current,
                faces,
                source_entities=source_entities or [],
                entity_source_ids=entity_source_ids,
                long_axis=long_axis,
                web_thickness=web_thickness,
                grid_size=grid_size,
            )
        )
        if strip_index is not None and strip_index not in used:
            used.append(strip_index)

    before_regularization = current
    regularization_semantics = (
        analyse_projection_boundary(
            current,
            source_entities,
            entity_source_ids=entity_source_ids,
            association_tolerance_mm=max(1e-7, grid_size * 0.51),
        )
        if source_entities is not None
        else None
    )
    regularized, regularization = _regularize_micro_topology(
        current,
        projection_semantics=regularization_semantics,
        fidelity_tolerance_mm=max(1e-7, grid_size * 0.51),
        long_axis=long_axis,
    )
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="web_boundary_completion",
        status="observed" if used else "not_applicable",
        title_zh="腹板纵向边界补全",
        summary_zh=(f"合并 {len(used)} 个端部边界面" if used else "无需合并端部边界面"),
        hypothesis_id=hypothesis_id,
        shapes=(
            polygon_shape("web-before-boundary-completion", "repair_removed", polygon),
            polygon_shape("web-after-boundary-completion", "repair_added", before_regularization),
        ),
        payload={
            "merged_face_indices": used,
            "grid_size_mm": grid_size,
            "parallel_terminal_strip_completion": parallel_completion,
        },
    )
    emit_trace(
        observer,
        stage_id="05_candidate_lowering",
        artifact_id="web_micro_regularization",
        status="observed" if regularization.get("applied") else "not_applicable",
        title_zh="腹板微拓扑规则化",
        summary_zh=("规则化已通过守卫并应用" if regularization.get("applied") else "规则化未应用"),
        hypothesis_id=hypothesis_id,
        shapes=(
            polygon_shape("web-before-regularization", "repair_removed", before_regularization),
            polygon_shape("web-after-regularization", "repair_added", regularized),
        ),
        payload=regularization,
    )
    return regularized, used, {
        "merged_face_indices": used,
        "parallel_terminal_strip_completion": parallel_completion,
        "regularization": regularization,
    }


def _clip_web_to_clear_height(
    polygon: Polygon,
    *,
    entities: list[DXFEntity],
    long_axis: str,
    flange_thickness: float,
    profile_height: float,
    nominal_length: float,
    tolerance_mm: float = 0.5,
) -> Polygon:
    """等高梁腹板净高校正：轮廓横向超净高时用翼缘厚度边界裁剪。

    腹板轮廓可能因翼缘端部未闭合而在多边形化时吸收下/上翼缘厚度。
    此函数利用 web 视图的近水平长线确定构件底面/顶面 transverse
    坐标，再按 profile 翼缘厚度定位净高范围。仅在证据充分时裁剪，
    否则保守保留原轮廓，避免为单一图纸过拟合。
    """
    bounds = polygon.bounds
    if long_axis == "x":
        long_min, lo, long_max, hi = bounds
    else:
        lo, long_min, hi, long_max = bounds
    clear_height = profile_height - 2.0 * flange_thickness
    if hi - lo <= clear_height + tolerance_mm:
        return polygon
    if hi - lo > clear_height + 2.5 * flange_thickness + tolerance_mm:
        # 超出"含 1~2 张翼缘厚度"的合理范围：这类轮廓多半来自端板/
        # 加劲区域或独立投影，而不是腹板吸收了翼缘厚度。保守保留，
        # 避免把真实几何裁剪到失败。
        return polygon

    transverse_lines: list[float] = []
    for entity in entities:
        if entity.dxftype() != "LINE":
            continue
        start = (float(entity.dxf.start.x), float(entity.dxf.start.y))
        end = (float(entity.dxf.end.x), float(entity.dxf.end.y))
        if long_axis == "x":
            span = abs(end[0] - start[0])
            t0, t1 = start[1], end[1]
        else:
            span = abs(end[1] - start[1])
            t0, t1 = start[0], end[0]
        if span < max(0.30 * nominal_length, 100.0):
            continue
        if abs(t1 - t0) > max(tolerance_mm, span * 1e-4):
            continue
        transverse_lines.append((t0 + t1) / 2.0)
    if len(transverse_lines) < 2:
        return polygon
    # Locate the pair of horizontal lines whose span matches the clear web
    # height.  A constant-height web keeps two explicit flange-to-web edges
    # (bottom+tf and top-tf); a cranked web has no such horizontal pair, so
    # it is deliberately left untouched.
    boundary_pairs = []
    for lo_t in transverse_lines:
        for hi_t in transverse_lines:
            if hi_t <= lo_t:
                continue
            span = hi_t - lo_t
            if abs(span - clear_height) <= max(tolerance_mm, 1.0):
                boundary_pairs.append((span, lo_t, hi_t))
    if not boundary_pairs:
        return polygon
    _, clip_lo, clip_hi = max(boundary_pairs, key=lambda item: item[0])
    if long_axis == "x":
        clip = box(long_min, clip_lo, long_max, clip_hi)
    else:
        clip = box(clip_lo, long_min, clip_hi, long_max)
    clipped = polygon.intersection(clip)
    if clipped.geom_type == "MultiPolygon":
        clipped = max(clipped.geoms, key=lambda item: item.area)
    elif clipped.geom_type == "GeometryCollection":
        polygons = [
            item
            for item in clipped.geoms
            if item.geom_type == "Polygon"
        ]
        if not polygons:
            return polygon
        clipped = max(polygons, key=lambda item: item.area)
    if clipped.is_empty or clipped.geom_type != "Polygon":
        return polygon
    clipped_bounds = clipped.bounds
    new_lo, new_hi = (
        (clipped_bounds[1], clipped_bounds[3])
        if long_axis == "x"
        else (clipped_bounds[0], clipped_bounds[2])
    )
    if abs((new_hi - new_lo) - clear_height) > max(2.0, flange_thickness):
        return polygon
    if clipped.area < 0.5 * polygon.area:
        return polygon
    # A stepped/cranked web may carry end height changes that this profile
    # check would otherwise clip away.  Require the clipped outline to remain
    # nearly a full clear-height rectangle: if a real step survived the line
    # pair, the fill ratio drops and we keep the original outline.
    if long_axis == "x":
        clipped_long = clipped_bounds[2] - clipped_bounds[0]
    else:
        clipped_long = clipped_bounds[3] - clipped_bounds[1]
    rect_area = clipped_long * clear_height
    if rect_area > 0.0 and clipped.area / rect_area < 0.90:
        return polygon
    return clipped


def select_web_polygon(
    entities: list[DXFEntity],
    *,
    entity_source_ids: tuple[str, ...] = (),
    profile_height: float,
    nominal_length: float,
    hole_centers: list[Point2D],
    source_bbox: BoundingBox,
    clear_web_height: float | None = None,
    web_thickness: float | None = None,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> PolygonizedResult:
    """Reconstruct a web, including separately noded longitudinal end faces.

    Hole-less webs are supported.  When holes exist, containment remains a
    strong semantic constraint.  A valid direct face is expanded only through
    adjacent end faces that extend its longitudinal envelope.

    ``clear_web_height`` is the profile's net web height (H - 2*tf).  When
    provided, the selected web outline is verified against it before being
    returned: a constant-height web that absorbed a flange thickness is
    corrected to its true net height instead of being shipped oversized.
    """
    long_axis = choose_long_axis(source_bbox, nominal_length)

    def _corrected(polygon: Polygon) -> Polygon:
        if clear_web_height is None:
            return polygon
        return _clip_web_to_clear_height(
            polygon,
            entities=entities,
            long_axis=long_axis,
            flange_thickness=(profile_height - clear_web_height) / 2.0,
            profile_height=profile_height,
            nominal_length=nominal_length,
        )

    grid_candidates = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1)
    failures: list[str] = []
    for grid_size in grid_candidates:
        faces = polygonize_part_entities(entities, grid_size)
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="web_precision_attempt",
            status="observed" if faces else "failed",
            title_zh="腹板精度网格尝试",
            summary_zh=f"grid={grid_size:g} mm 得到 {len(faces)} 个面",
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes(f"web-grid-{grid_size:g}", "face_candidate", faces),
            payload={"grid_size_mm": grid_size, "face_count": len(faces)},
        )
        selected: list[Polygon] = []
        for face in faces:
            length, transverse = _axis_lengths(face.bounds, long_axis)
            if transverse < 0.45 * profile_height:
                continue
            if length < max(0.03 * nominal_length, 100.0):
                continue
            selected.append(face)
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="web_faces",
            status="observed" if selected else "failed",
            title_zh="腹板候选面",
            summary_zh=f"{len(faces)} 个面中 {len(selected)} 个满足腹板尺寸门限",
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes(f"web-face-{grid_size:g}", "face_candidate", faces),
            payload={
                "grid_size_mm": grid_size,
                "face_count": len(faces),
                "large_face_count": len(selected),
            },
        )
        if not selected:
            failures.append(f"grid={grid_size}: no large faces")
            continue

        cover_tolerance = max(0.05, grid_size * 3.0)
        direct = [
            face
            for face in selected
            if all(face.buffer(cover_tolerance).covers(Point(p.x, p.y)) for p in hole_centers)
        ]
        # With no holes, the largest large face is the most conservative seed.
        if not hole_centers:
            direct = selected[:]
        if direct:
            direct.sort(key=lambda face: face.area, reverse=True)
            base = direct[0]
            projection_repairs: list[dict[str, object]] = []
            emit_trace(
                observer,
                stage_id="05_candidate_lowering",
                artifact_id="web_seed",
                status="observed",
                title_zh="腹板种子面",
                summary_zh=f"选取面积最大的 {len(direct)} 个直接候选中的首个作为种子",
                hypothesis_id=hypothesis_id,
                shapes=(polygon_shape("web-seed", "face_selected", base),),
                payload={
                    "grid_size_mm": grid_size,
                    "direct_candidate_count": len(direct),
                    "hole_count": len(hole_centers),
                },
            )
            if not hole_centers:
                emit_trace(
                    observer,
                    stage_id="05_candidate_lowering",
                    artifact_id="holeless_web_selection",
                    status="observed",
                    title_zh="无孔腹板选择",
                    summary_zh="无物理圆孔约束，按最大合格面选择保守种子。",
                    hypothesis_id=hypothesis_id,
                    shapes=(polygon_shape("holeless-web-seed", "face_selected", base),),
                    payload={"selection_rule": "largest_qualified_face"},
                )
            expanded, expanded_indices = _expand_at_longitudinal_ends(
                base,
                faces,
                long_axis=long_axis,
                minimum_transverse=0.40 * profile_height,
                maximum_transverse=_axis_lengths(base.bounds, long_axis)[1] + max(1.0, 0.02 * profile_height),
                grid_size=grid_size,
            )
            expansion_semantics = analyse_projection_boundary(
                expanded,
                entities,
                entity_source_ids=entity_source_ids,
                association_tolerance_mm=max(1e-7, grid_size * 0.51),
            )
            expanded = _clean_candidate_polygon(
                expanded,
                grid_size=grid_size,
                projection_semantics=expansion_semantics,
                repair_diagnostics=projection_repairs,
                fidelity_tolerance_mm=max(1e-7, grid_size * 0.51),
            )
            emit_trace(
                observer,
                stage_id="05_candidate_lowering",
                artifact_id="web_end_expansion",
                status="observed" if expanded_indices else "not_applicable",
                title_zh="腹板端部扩展",
                summary_zh=(f"吸收 {len(expanded_indices)} 个端部面" if expanded_indices else "种子无需端部扩展"),
                hypothesis_id=hypothesis_id,
                shapes=(
                    polygon_shape("web-before-end-expansion", "repair_removed", base),
                    polygon_shape("web-after-end-expansion", "repair_added", expanded),
                ),
                payload={"expanded_face_indices": expanded_indices},
            )
            hidden_faces = _polygonize_part_entities_with_hidden(entities, grid_size)
            completion_faces = faces + hidden_faces
            expanded, completion_indices, completion_diagnostics = _complete_longitudinal_boundary_faces(
                expanded,
                completion_faces,
                source_entities=entities,
                entity_source_ids=entity_source_ids,
                long_axis=long_axis,
                profile_height=profile_height,
                nominal_length=nominal_length,
                web_thickness=web_thickness,
                grid_size=grid_size,
                observer=observer,
                hypothesis_id=hypothesis_id,
            )
            before_hidden_bridge = expanded
            expanded = _merge_boundary_bridge_faces(
                expanded,
                hidden_faces,
                long_axis=long_axis,
                profile_height=profile_height,
                nominal_length=nominal_length,
                grid_size=grid_size,
            )
            bridge_semantics = analyse_projection_boundary(
                expanded,
                entities,
                entity_source_ids=entity_source_ids,
                association_tolerance_mm=max(1e-7, grid_size * 0.51),
            )
            expanded = _clean_candidate_polygon(
                expanded,
                grid_size=grid_size,
                projection_semantics=bridge_semantics,
                repair_diagnostics=projection_repairs,
                fidelity_tolerance_mm=max(1e-7, grid_size * 0.51),
            )
            hidden_changed = not _polygon_nearly_equal(
                before_hidden_bridge,
                expanded,
                distance_tolerance=max(0.001, grid_size),
                area_ratio_tolerance=1e-6,
            )
            emit_trace(
                observer,
                stage_id="05_candidate_lowering",
                artifact_id="web_hidden_bridge",
                status="observed" if hidden_changed else "not_applicable",
                title_zh="腹板隐藏线桥接",
                summary_zh=("隐藏线证据补全了外边界" if hidden_changed else "隐藏线未改变已建立边界"),
                hypothesis_id=hypothesis_id,
                shapes=(
                    polygon_shape("web-before-hidden-bridge", "repair_removed", before_hidden_bridge),
                    polygon_shape("web-after-hidden-bridge", "repair_added", expanded),
                ),
                payload={"hidden_face_count": len(hidden_faces)},
            )
            if all(expanded.buffer(cover_tolerance).covers(Point(p.x, p.y)) for p in hole_centers):
                length, transverse = _axis_lengths(expanded.bounds, long_axis)
                boundary_conservation = assess_selected_projection_boundary(
                    expanded,
                    entities,
                    entity_source_ids=entity_source_ids,
                    association_tolerance_mm=max(1e-7, grid_size * 0.51),
                    fidelity_tolerance_mm=max(1e-7, grid_size * 0.51),
                )
                emit_trace(
                    observer,
                    stage_id="05_candidate_lowering",
                    artifact_id="web_selected",
                    status="selected",
                    title_zh="腹板轮廓选定",
                    summary_zh=f"选定腹板：{length:.3f}×{transverse:.3f} mm",
                    hypothesis_id=hypothesis_id,
                    shapes=(polygon_shape("web-selected", "face_selected", expanded),),
                    payload={
                        "grid_size_mm": grid_size,
                        "length_mm": length,
                        "transverse_mm": transverse,
                        "hole_count": len(hole_centers),
                    },
                )
                return PolygonizedResult(
                    _corrected(expanded),
                    grid_size,
                    faces,
                    {
                        "mode": "direct_face_with_end_expansion",
                        "long_axis": long_axis,
                        "face_count": len(faces),
                        "large_face_count": len(selected),
                        "direct_candidate_count": len(direct),
                        "selected_area_mm2": float(expanded.area),
                        "selected_length_mm": length,
                        "selected_transverse_mm": transverse,
                        "expanded_face_indices": expanded_indices,
                        "boundary_completion_face_indices": completion_indices,
                        "boundary_completion": completion_diagnostics,
                        "hole_count_used_as_constraint": len(hole_centers),
                        "projection_boundary_repairs": projection_repairs,
                        "projection_boundary_conservation": (
                            boundary_conservation.to_dict()
                        ),
                    },
                )

        merged = unary_union(selected)
        if isinstance(merged, MultiPolygon):
            merged = unary_union([polygon.buffer(grid_size * 1.5) for polygon in merged.geoms]).buffer(-grid_size * 1.5)
        if not isinstance(merged, Polygon):
            failures.append(f"grid={grid_size}: merged geometry is {merged.geom_type}")
            continue
        if not all(merged.buffer(cover_tolerance).covers(Point(p.x, p.y)) for p in hole_centers):
            failures.append(f"grid={grid_size}: not all holes are enclosed")
            continue
        cleaning_repairs: list[dict[str, object]] = []
        cleaning_semantics = analyse_projection_boundary(
            merged,
            entities,
            entity_source_ids=entity_source_ids,
            association_tolerance_mm=max(1e-7, grid_size * 0.51),
        )
        cleaned = _clean_candidate_polygon(
            merged,
            grid_size=grid_size,
            projection_semantics=cleaning_semantics,
            repair_diagnostics=cleaning_repairs,
            fidelity_tolerance_mm=max(1e-7, grid_size * 0.51),
        )
        length, transverse = _axis_lengths(cleaned.bounds, long_axis)
        boundary_conservation = assess_selected_projection_boundary(
            cleaned,
            entities,
            entity_source_ids=entity_source_ids,
            association_tolerance_mm=max(1e-7, grid_size * 0.51),
            fidelity_tolerance_mm=max(1e-7, grid_size * 0.51),
        )
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="web_selected",
            status="selected",
            title_zh="腹板轮廓选定",
            summary_zh=f"合并大面后选定腹板：{length:.3f}×{transverse:.3f} mm",
            hypothesis_id=hypothesis_id,
            shapes=(polygon_shape("web-selected", "face_selected", cleaned),),
            payload={
                "grid_size_mm": grid_size,
                "mode": "merged_large_faces",
                "length_mm": length,
                "transverse_mm": transverse,
                "hole_count": len(hole_centers),
            },
        )
        return PolygonizedResult(
            _corrected(cleaned),
            grid_size,
            faces,
            {
                "mode": "merged_large_faces",
                "long_axis": long_axis,
                "face_count": len(faces),
                "large_face_count": len(selected),
                "direct_candidate_count": len(direct),
                "selected_area_mm2": float(cleaned.area),
                "selected_length_mm": length,
                "selected_transverse_mm": transverse,
                "hole_count_used_as_constraint": len(hole_centers),
                "failures_before_success": list(failures),
                "projection_boundary_repairs": cleaning_repairs,
                "projection_boundary_conservation": (
                    boundary_conservation.to_dict()
                ),
            },
        )
    raise ValueError("Could not reconstruct the web polygon; " + "; ".join(failures))


def select_flange_polygons(
    entities: list[DXFEntity],
    *,
    entity_source_ids: tuple[str, ...] = (),
    annotation_masks: tuple[ProjectionAnnotationMask, ...] = (),
    flange_width: float,
    nominal_length: float,
    source_bbox: BoundingBox,
    main_flange_spans: dict[str, float] | None = None,
    manufacturing_tolerance_mm: float = 0.15,
    observer: TraceObserver | None = None,
    hypothesis_id: str | None = None,
) -> tuple[list[Polygon], float, dict[str, object]]:
    """Select one or two physical flange outlines from overlapping projections.

    The algorithm first obtains near-full-width seed faces, grows each seed
    through adjacent longitudinal end faces, deduplicates identical expanded
    outlines, and retains a distinct seed only when it is large enough to be a
    second physical flange.  This handles both split end faces and overlapping
    top/bottom flange projections without enumerating sample-specific cases.
    """
    long_axis = choose_long_axis(source_bbox, nominal_length)
    inferred_linework, annotation_gap_repairs = _annotation_masked_projection_bridges(
        entities,
        annotation_masks=annotation_masks,
        long_axis=long_axis,
        flange_width=flange_width,
        source_bbox=source_bbox,
    )
    source_loss_retry_count = 0
    for grid_size in (0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1):
        faces = polygonize_part_entities(
            entities,
            grid_size,
            inferred_linework=inferred_linework,
        )
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_precision_attempt",
            status="observed" if faces else "failed",
            title_zh="翼缘精度网格尝试",
            summary_zh=f"grid={grid_size:g} mm 得到 {len(faces)} 个面",
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes(f"flange-grid-{grid_size:g}", "face_candidate", faces),
            payload={"grid_size_mm": grid_size, "face_count": len(faces)},
        )
        selected = [
            face
            for face in faces
            if _near_full_flange_width(face, long_axis=long_axis, flange_width=flange_width)
        ]
        if not selected:
            continue
        # Keep each full-width atomic face as an independent projection
        # hypothesis. Joining touching faces here makes overlapping physical
        # flange outlines depend on the absolute phase of GEOS' precision
        # grid; expansion and canonical deduplication below are the proper
        # stages to converge equivalent seeds.
        seeds = sorted(selected, key=lambda face: (-face.area, face.bounds))
        seeds = [
            component
            for component in seeds
            if _axis_lengths(component.bounds, long_axis)[0] >= max(0.10 * nominal_length, 100.0)
        ]
        if not seeds:
            continue

        seeds = [
            _complete_flange_seed_end_caps(
                seed,
                faces,
                long_axis=long_axis,
                flange_width=flange_width,
                nominal_length=nominal_length,
                grid_size=grid_size,
            )
            for seed in seeds
        ]
        rectangular_repairs = [
            _reconstruct_proven_rectangular_projection(
                component,
                entities,
                entity_source_ids=entity_source_ids,
            )
            for component in seeds
        ]
        seeds = [decision.polygon for decision in rectangular_repairs]
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_seeds",
            status="observed",
            title_zh="翼缘种子面",
            summary_zh=f"建立 {len(seeds)} 个近全宽翼缘种子",
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes("flange-seed", "face_candidate", seeds),
            payload={
                "grid_size_mm": grid_size,
                "seed_count": len(seeds),
                "long_axis": long_axis,
                "projection_boundary_repairs": [
                    decision.to_dict() for decision in rectangular_repairs
                ],
            },
        )
        end_records: list[tuple[Polygon, list[int], Polygon]] = []
        for seed in seeds:
            expanded, used = _expand_at_longitudinal_ends(
                seed,
                faces,
                long_axis=long_axis,
                minimum_transverse=0.25 * flange_width,
                maximum_transverse=1.08 * flange_width,
                grid_size=grid_size,
            )
            end_records.append((expanded, used, seed))
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_end_expansion",
            status=(
                "observed" if any(record[1] for record in end_records)
                else "not_applicable"
            ),
            title_zh="翼缘端部扩展",
            summary_zh=f"完成 {len(end_records)} 个种子的端部连通扩展",
            hypothesis_id=hypothesis_id,
            shapes=(
                *polygon_shapes("flange-before-expansion", "repair_removed", seeds),
                *polygon_shapes(
                    "flange-after-expansion",
                    "repair_added",
                    (record[0] for record in end_records),
                ),
            ),
            payload={
                "used_face_indices": [record[1] for record in end_records],
            },
        )

        # Completing every connected projection can absorb a genuinely separate
        # flange.  We only complete when two independent seeds converge to the
        # same end-expanded envelope; that is strong evidence of one overlapping
        # physical outline partitioned by projection edges.
        consensus_indices: set[int] = set()
        for first in range(len(end_records)):
            for second in range(first + 1, len(end_records)):
                if _polygon_nearly_equal(
                    end_records[first][0],
                    end_records[second][0],
                    distance_tolerance=0.05,
                    area_ratio_tolerance=5e-4,
                ):
                    consensus_indices.update({first, second})
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_projection_consensus",
            status="observed" if consensus_indices else "not_applicable",
            title_zh="翼缘投影一致性",
            summary_zh=(
                f"{len(consensus_indices)} 个种子收敛到同一投影包络"
                if consensus_indices
                else "未出现需要连通补全的重合投影共识"
            ),
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes(
                "flange-consensus", "face_candidate", (record[0] for record in end_records)
            ),
            payload={"consensus_seed_indices": sorted(consensus_indices)},
        )

        expanded_records: list[tuple[Polygon, list[int], Polygon]] = []
        for index, (expanded, used, seed) in enumerate(end_records):
            if index in consensus_indices:
                expanded, connected_used = _complete_connected_projection(
                    expanded,
                    faces,
                    long_axis=long_axis,
                    flange_width=flange_width,
                    nominal_length=nominal_length,
                    grid_size=grid_size,
                )
                used = sorted(set(used + connected_used))
            expanded_records.append((expanded, used, seed))

        expanded_unique: list[Polygon] = []
        for expanded, _, _ in sorted(expanded_records, key=lambda item: item[0].area, reverse=True):
            if any(_polygon_nearly_equal(expanded, existing) for existing in expanded_unique):
                continue
            expanded_unique.append(expanded)

        nested_projection_classification = "not_applicable"
        # Physical drawings contain at most the two H-section flange plates.
        chosen: list[Polygon] = expanded_unique[:2]
        if len(chosen) == 1 and main_flange_spans:
            recovered = _recover_source_backed_nested_flange_pair(
                primary=chosen[0],
                seeds=seeds,
                entities=entities,
                entity_source_ids=entity_source_ids,
                long_axis=long_axis,
                flange_width=flange_width,
                main_flange_spans=main_flange_spans,
                grid_size=grid_size,
                manufacturing_tolerance_mm=manufacturing_tolerance_mm,
                faces=faces,
                nominal_length=nominal_length,
            )
            if recovered is not None:
                chosen = list(recovered)
                nested_projection_classification = "physical_pair"
            elif len(seeds) > 1 or any(
                not _polygon_nearly_equal(seed, chosen[0]) for seed in seeds
            ):
                nested_projection_classification = "projection_artifact"
        direct_source_edge_loss = any(
            decision.to_dict().get("reason") == "direct_source_edge_loss"
            for decision in rectangular_repairs
        )
        nested_oblique_source_course = any(
            entity.dxftype() == "LINE"
            and entity.dxf.layer == "Part"
            and entity.dxf.linetype != "XKITLINE04"
            and abs(float(entity.dxf.end.x - entity.dxf.start.x))
            > manufacturing_tolerance_mm
            and abs(float(entity.dxf.end.y - entity.dxf.start.y))
            > manufacturing_tolerance_mm
            and Point(
                float(entity.dxf.start.x),
                float(entity.dxf.start.y),
            ).distance(chosen[0].boundary)
            <= manufacturing_tolerance_mm
            and Point(
                float(entity.dxf.end.x),
                float(entity.dxf.end.y),
            ).distance(chosen[0].boundary)
            <= manufacturing_tolerance_mm
            and chosen[0].buffer(manufacturing_tolerance_mm).covers(
                LineString(
                    (
                        (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                        (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                    )
                )
            )
            for entity in entities
        )
        if (
            nested_projection_classification != "physical_pair"
            and direct_source_edge_loss
            and source_loss_retry_count
            < (2 if nested_oblique_source_course else 1)
        ):
            source_loss_retry_count += 1
            continue
        if not chosen:
            continue
        cleaning_repairs: list[dict[str, object]] = []
        cleaned_chosen: list[Polygon] = []
        for item in chosen:
            cleaning_semantics = analyse_projection_boundary(
                item,
                entities,
                entity_source_ids=entity_source_ids,
                association_tolerance_mm=max(1e-7, grid_size * 0.51),
            )
            cleaned_chosen.append(
                _clean_candidate_polygon(
                    item,
                    grid_size=grid_size,
                    projection_semantics=cleaning_semantics,
                    repair_diagnostics=cleaning_repairs,
                    fidelity_tolerance_mm=max(1e-7, grid_size * 0.51),
                )
            )
        chosen = cleaned_chosen
        if long_axis == "x":
            chosen.sort(key=lambda polygon: (-polygon.area, polygon.bounds[0]))
        else:
            chosen.sort(key=lambda polygon: (-polygon.area, polygon.bounds[1]))
        boundary_conservation = [
            assess_selected_projection_boundary(
                polygon,
                entities,
                entity_source_ids=entity_source_ids,
                association_tolerance_mm=max(1e-7, grid_size * 0.51),
                fidelity_tolerance_mm=max(1e-7, grid_size * 0.51),
            )
            for polygon in chosen
        ]
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_second_plate",
            status="observed" if len(chosen) == 2 else "not_applicable",
            title_zh="第二块翼缘板识别",
            summary_zh=(
                "保留两个不同的物理翼缘轮廓。"
                if len(chosen) == 2
                else "单一投影轮廓代表两块同形翼缘板。"
            ),
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes("flange-physical", "face_selected", chosen),
            payload={"selected_geometry_count": len(chosen)},
        )
        diagnostics = {
            "grid_size_mm": grid_size,
            "long_axis": long_axis,
            "seed_count": len(seeds),
            "seed_lengths_mm": [_axis_lengths(seed.bounds, long_axis)[0] for seed in seeds],
            "expanded_lengths_mm": [_axis_lengths(item[0].bounds, long_axis)[0] for item in expanded_records],
            "selected_lengths_mm": [_axis_lengths(item.bounds, long_axis)[0] for item in chosen],
            "expanded_face_indices": [item[1] for item in expanded_records],
            "nested_projection_classification": nested_projection_classification,
            "selected_rail_lengths_mm": [
                list(rails)
                if (
                    rails := _longitudinal_rail_lengths(
                        item,
                        long_axis=long_axis,
                        tolerance_mm=manufacturing_tolerance_mm,
                    )
                )
                is not None
                else []
                for item in chosen
            ],
            "nested_pair_source_conserved": (
                nested_projection_classification == "physical_pair"
            ),
            "nested_equal_span_role_authority": (
                nested_projection_classification == "physical_pair"
                and main_flange_spans is not None
                and main_flange_spans.get("high") is not None
                and main_flange_spans.get("low") is not None
                and abs(
                    float(main_flange_spans["high"])
                    - float(main_flange_spans["low"])
                )
                <= manufacturing_tolerance_mm
            ),
            "projection_boundary_repairs": [
                decision.to_dict() for decision in rectangular_repairs
            ] + cleaning_repairs,
            "annotation_masked_projection_gaps": annotation_gap_repairs,
            "projection_boundary_conservation": [
                decision.to_dict() for decision in boundary_conservation
            ],
        }
        emit_trace(
            observer,
            stage_id="05_candidate_lowering",
            artifact_id="flange_selected",
            status="selected",
            title_zh="翼缘轮廓选定",
            summary_zh=f"选定 {len(chosen)} 个翼缘几何轮廓",
            hypothesis_id=hypothesis_id,
            shapes=polygon_shapes("flange-selected", "face_selected", chosen),
            payload=diagnostics,
        )
        return chosen, grid_size, diagnostics
    raise ValueError("Could not reconstruct any full-width flange plate polygon.")


def flatten_bulge_contour(
    contour: BulgeContour,
    *,
    max_sagitta: float = 0.005,
    max_angle_step_degrees: float = 2.0,
) -> list[tuple[float, float]]:
    """Flatten a bulge contour without allocating a temporary DXF document.

    This avoids retaining cyclic `Drawing` object graphs during large batch
    runs.  The returned point order follows the contour direction,
    including clockwise arcs represented by a negative bulge.
    """
    from math import acos, atan2 as _atan2, ceil as _ceil, cos as _cos, pi, sin as _sin
    from ezdxf.math import bulge_to_arc

    vertices = contour.vertices
    points: list[tuple[float, float]] = []
    for index, vertex in enumerate(vertices):
        following = vertices[(index + 1) % len(vertices)]
        start = (float(vertex.x), float(vertex.y))
        end = (float(following.x), float(following.y))
        if not points or hypot(points[-1][0] - start[0], points[-1][1] - start[1]) > 1e-12:
            points.append(start)
        bulge = float(vertex.bulge)
        if abs(bulge) <= 1e-12:
            continue
        center, _, _, radius = bulge_to_arc(start, end, bulge)
        sweep = 4.0 * atan2(abs(bulge), 1.0)
        direction = 1.0 if bulge > 0 else -1.0
        start_angle = _atan2(start[1] - center.y, start[0] - center.x)
        if radius > max_sagitta > 0:
            sagitta_step = 2.0 * acos(max(-1.0, min(1.0, 1.0 - max_sagitta / radius)))
        else:
            sagitta_step = pi
        angle_step = min(sagitta_step, max_angle_step_degrees * pi / 180.0)
        count = max(2, int(_ceil(sweep / max(angle_step, 1e-9))))
        for step in range(1, count):
            angle = start_angle + direction * sweep * step / count
            points.append(
                (
                    float(center.x) + radius * _cos(angle),
                    float(center.y) + radius * _sin(angle),
                )
            )
    return points
