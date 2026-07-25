from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import atan2, cos, degrees, hypot, radians, sin
from typing import Iterable

from ezdxf.entities import DXFEntity
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

from .geometry_types import Point2D


class ProjectionEdgeAuthority(str, Enum):
    """How a selected projection-boundary edge entered the compiler."""

    DIRECT = "direct"
    PROJECTION_OVERLAY = "projection_overlay"
    INFERRED = "inferred"


@dataclass(frozen=True, slots=True)
class ProjectedBoundaryEdge:
    """A source geometry edge associated with one selected plate boundary."""

    start: Point2D
    end: Point2D
    authority: ProjectionEdgeAuthority
    source_ids: tuple[str, ...]

    @property
    def line(self) -> LineString:
        return LineString(((self.start.x, self.start.y), (self.end.x, self.end.y)))


@dataclass(frozen=True, slots=True)
class ProjectionBoundarySemantics:
    """Direct Tekla/DXF evidence close enough to explain a selected boundary."""

    direct_edges: tuple[ProjectedBoundaryEdge, ...]
    association_tolerance_mm: float

    @property
    def protected_source_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    source_id
                    for edge in self.direct_edges
                    for source_id in edge.source_ids
                }
            )
        )


@dataclass(frozen=True, slots=True)
class BoundaryRepairDecision:
    """Result of treating a numerical geometry change as a proof candidate."""

    polygon: Polygon
    applied: bool
    reason: str
    repair_kind: str
    protected_source_ids: tuple[str, ...]
    lost_source_ids: tuple[str, ...]
    fidelity_tolerance_mm: float
    reclassified_source_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_kind": self.repair_kind,
            "applied": self.applied,
            "reason": self.reason,
            "protected_source_ids": list(self.protected_source_ids),
            "lost_source_ids": list(self.lost_source_ids),
            "fidelity_tolerance_mm": self.fidelity_tolerance_mm,
            "reclassified_source_ids": list(self.reclassified_source_ids),
        }


def _source_id(entity: DXFEntity, index: int, source_ids: tuple[str, ...]) -> str:
    if index < len(source_ids) and source_ids[index]:
        return source_ids[index]
    handle = str(getattr(entity.dxf, "handle", "") or "")
    return f"dxf:{handle or index}"


def _visible_source_lines(
    entities: Iterable[DXFEntity],
    source_ids: tuple[str, ...],
) -> Iterable[tuple[LineString, str]]:
    for index, entity in enumerate(entities):
        if entity.dxftype() != "LINE":
            continue
        if str(getattr(entity.dxf, "layer", "")).casefold() != "part":
            continue
        if str(getattr(entity.dxf, "linetype", "")).upper() == "XKITLINE04":
            continue
        start = entity.dxf.start
        end = entity.dxf.end
        if hypot(float(end.x - start.x), float(end.y - start.y)) <= 1e-12:
            continue
        yield (
            LineString(
                (
                    (float(start.x), float(start.y)),
                    (float(end.x), float(end.y)),
                )
            ),
            _source_id(entity, index, source_ids),
        )


def _source_arc_courses(
    entities: Iterable[DXFEntity],
) -> tuple[tuple[float, float, float, float, float], ...]:
    return tuple(
        (
            float(entity.dxf.center.x),
            float(entity.dxf.center.y),
            float(entity.dxf.radius),
            float(entity.dxf.start_angle),
            float(entity.dxf.end_angle),
        )
        for entity in entities
        if entity.dxftype() == "ARC"
        and str(getattr(entity.dxf, "layer", "")).casefold() == "part"
    )


def _line_is_arc_transition_chord(
    line: LineString,
    arc_courses: tuple[tuple[float, float, float, float, float], ...],
    *,
    tolerance: float,
) -> bool:
    """Recognize short Tekla LINE continuations belonging to an ARC course."""

    start = line.coords[0]
    end = line.coords[-1]
    for center_x, center_y, radius, start_angle, end_angle in arc_courses:
        if line.length > 0.35 * radius:
            continue
        start_radius = hypot(start[0] - center_x, start[1] - center_y)
        end_radius = hypot(end[0] - center_x, end[1] - center_y)
        arc_start = (
            center_x + radius * cos(radians(start_angle)),
            center_y + radius * sin(radians(start_angle)),
        )
        arc_end = (
            center_x + radius * cos(radians(end_angle)),
            center_y + radius * sin(radians(end_angle)),
        )
        touches_arc_endpoint = min(
            hypot(start[0] - point[0], start[1] - point[1])
            for point in (arc_start, arc_end)
        ) <= tolerance or min(
            hypot(end[0] - point[0], end[1] - point[1])
            for point in (arc_start, arc_end)
        ) <= tolerance
        if (
            abs(start_radius - radius) <= tolerance
            and abs(end_radius - radius) <= tolerance
            and touches_arc_endpoint
        ):
            return True
    return False


def _line_is_covered(
    line: LineString,
    boundary: LineString,
    *,
    tolerance: float,
) -> bool:
    if line.distance(boundary) > tolerance:
        return False
    band = boundary.buffer(tolerance, cap_style=2, join_style=2)
    outside = line.difference(band)
    return outside.is_empty or outside.length <= max(1e-12, tolerance)


def _angle_mod_180(line: LineString) -> float:
    start = line.coords[0]
    end = line.coords[-1]
    return degrees(atan2(end[1] - start[1], end[0] - start[0])) % 180.0


def _angle_distance_180(first: float, second: float) -> float:
    delta = abs(first - second) % 180.0
    return min(delta, 180.0 - delta)


def _candidate_boundary_segments(candidate: Polygon) -> Iterable[LineString]:
    rings = (candidate.exterior, *candidate.interiors)
    for ring in rings:
        coordinates = list(ring.coords)
        for start, end in zip(coordinates, coordinates[1:]):
            segment = LineString((start, end))
            if segment.length > 1e-12:
                yield segment


def _source_line_is_preserved(
    line: LineString,
    candidate: Polygon,
    *,
    tolerance: float,
) -> bool:
    machine_tolerance = min(tolerance, 1e-7)
    if _line_is_covered(
        line,
        candidate.boundary,
        tolerance=machine_tolerance,
    ):
        return True
    if not _line_is_covered(line, candidate.boundary, tolerance=tolerance):
        return False
    source_angle = _angle_mod_180(line)
    # Endpoint quantization can rotate a short segment even when the physical
    # course is unchanged.  Derive angular uncertainty from the same declared
    # coordinate tolerance; do not introduce a second manufacturing threshold.
    angular_tolerance_degrees = max(
        1e-5,
        degrees(2.0 * tolerance / max(line.length, tolerance)),
    )
    return any(
        segment.distance(line) <= tolerance
        and _angle_distance_180(source_angle, _angle_mod_180(segment))
        <= angular_tolerance_degrees
        for segment in _candidate_boundary_segments(candidate)
    )


def analyse_projection_boundary(
    polygon: Polygon,
    entities: Iterable[DXFEntity],
    *,
    entity_source_ids: tuple[str, ...] = (),
    association_tolerance_mm: float = 0.15,
) -> ProjectionBoundarySemantics:
    """Associate visible source LINEs with a selected projection boundary.

    Association tolerates polygonization displacement, but the returned edge
    stores the exact source coordinates.  A later repair must therefore prove
    fidelity against the source line itself, not against a snapped chord.
    """

    if association_tolerance_mm <= 0:
        raise ValueError("association_tolerance_mm must be positive")
    materialized_entities = tuple(entities)
    boundary = polygon.boundary
    arc_courses = _source_arc_courses(materialized_entities)
    direct: list[ProjectedBoundaryEdge] = []
    for line, source_id in _visible_source_lines(
        materialized_entities,
        entity_source_ids,
    ):
        if _line_is_arc_transition_chord(
            line,
            arc_courses,
            tolerance=association_tolerance_mm,
        ):
            continue
        if not _line_is_covered(
            line,
            boundary,
            tolerance=association_tolerance_mm,
        ):
            continue
        start = line.coords[0]
        end = line.coords[-1]
        direct.append(
            ProjectedBoundaryEdge(
                start=Point2D(float(start[0]), float(start[1])),
                end=Point2D(float(end[0]), float(end[1])),
                authority=ProjectionEdgeAuthority.DIRECT,
                source_ids=(source_id,),
            )
        )
    return ProjectionBoundarySemantics(
        direct_edges=tuple(direct),
        association_tolerance_mm=association_tolerance_mm,
    )


def lost_direct_source_ids(
    candidate: Polygon,
    semantics: ProjectionBoundarySemantics,
    *,
    fidelity_tolerance_mm: float = 1e-7,
) -> tuple[str, ...]:
    """Return direct source IDs no longer lying on ``candidate`` boundary."""

    if fidelity_tolerance_mm <= 0:
        raise ValueError("fidelity_tolerance_mm must be positive")
    lost = {
        source_id
        for edge in semantics.direct_edges
        if not _source_line_is_preserved(
            edge.line,
            candidate,
            tolerance=fidelity_tolerance_mm,
        )
        for source_id in edge.source_ids
    }
    return tuple(sorted(lost))


def evaluate_boundary_repair(
    original: Polygon,
    candidate: Polygon,
    semantics: ProjectionBoundarySemantics,
    *,
    fidelity_tolerance_mm: float = 1e-7,
    repair_kind: str,
) -> BoundaryRepairDecision:
    """Accept a numerical repair only when all direct boundary edges survive."""

    lost = lost_direct_source_ids(
        candidate,
        semantics,
        fidelity_tolerance_mm=fidelity_tolerance_mm,
    )
    applied = not lost
    return BoundaryRepairDecision(
        polygon=candidate if applied else original,
        applied=applied,
        reason="source_edges_conserved" if applied else "direct_source_edge_loss",
        repair_kind=repair_kind,
        protected_source_ids=semantics.protected_source_ids,
        lost_source_ids=lost,
        fidelity_tolerance_mm=fidelity_tolerance_mm,
    )


def _longitudinal_reversal_overlay_zone(
    polygon: Polygon,
    *,
    long_axis: str,
    maximum_separation: float,
    minimum_overlap_ratio: float,
):
    """Return narrow bands occupied by anti-parallel boundary U-turns."""

    if long_axis not in {"x", "y"}:
        return None
    coordinates = list(polygon.exterior.coords)
    segments: list[tuple[LineString, float, float, float, float]] = []
    for start, end in zip(coordinates, coordinates[1:]):
        dx = float(end[0] - start[0])
        dy = float(end[1] - start[1])
        length = hypot(dx, dy)
        if length <= 1e-12:
            continue
        longitudinal = dx if long_axis == "x" else dy
        transverse = dy if long_axis == "x" else dx
        if abs(transverse) > 1e-6 * length:
            continue
        start_long = float(start[0] if long_axis == "x" else start[1])
        end_long = float(end[0] if long_axis == "x" else end[1])
        transverse_position = float(
            (start[1] + end[1]) / 2.0
            if long_axis == "x"
            else (start[0] + end[0]) / 2.0
        )
        segments.append(
            (
                LineString((start, end)),
                min(start_long, end_long),
                max(start_long, end_long),
                transverse_position,
                longitudinal / length,
            )
        )

    axis_span = (
        float(polygon.bounds[2] - polygon.bounds[0])
        if long_axis == "x"
        else float(polygon.bounds[3] - polygon.bounds[1])
    )
    qualifying_zones = []
    for first_index, first in enumerate(segments):
        for second in segments[first_index + 1 :]:
            if first[4] * second[4] >= -0.999999:
                continue
            separation = abs(first[3] - second[3])
            if separation > maximum_separation:
                continue
            overlap = min(first[2], second[2]) - max(first[1], second[1])
            if overlap < max(
                5.0,
                10.0 * separation,
                minimum_overlap_ratio * axis_span,
            ):
                continue
            overlap_low = max(first[1], second[1])
            overlap_high = min(first[2], second[2])
            longitudinal_padding = max(5.0, 25.0 * maximum_separation)
            transverse_low = min(first[3], second[3]) - 1.05 * maximum_separation
            transverse_high = max(first[3], second[3]) + 1.05 * maximum_separation
            if long_axis == "x":
                qualifying_zones.append(
                    box(
                        overlap_low - longitudinal_padding,
                        transverse_low,
                        overlap_high + longitudinal_padding,
                        transverse_high,
                    )
                )
            else:
                qualifying_zones.append(
                    box(
                        transverse_low,
                        overlap_low - longitudinal_padding,
                        transverse_high,
                        overlap_high + longitudinal_padding,
                    )
                )

    if not qualifying_zones:
        return None
    return unary_union(qualifying_zones)


def evaluate_longitudinal_projection_overlay_repair(
    original: Polygon,
    candidate: Polygon,
    semantics: ProjectionBoundarySemantics,
    *,
    long_axis: str,
    maximum_separation: float,
    fidelity_tolerance_mm: float = 1e-7,
    minimum_overlap_ratio: float = 0.0,
    minimum_continuing_course_ratio: float = 0.50,
    repair_kind: str = "micro_topology_regularization",
) -> BoundaryRepairDecision:
    """Reclassify only source edges belonging to a Tekla projection overlay.

    A source-first compiler normally protects every visible ``Part`` line on a
    selected boundary.  The exception is a narrow U-turn crossed by a source
    course that continues along most of the accepted member boundary: that is
    a projected face/visibility overlay, not a second fabrication cut.  Every
    removed fragment must lie inside the exact U-turn band; an unrelated bevel
    therefore keeps the repair fail-closed.
    """

    decision = evaluate_boundary_repair(
        original,
        candidate,
        semantics,
        fidelity_tolerance_mm=fidelity_tolerance_mm,
        repair_kind=repair_kind,
    )
    if decision.applied:
        return decision
    overlay_zone = _longitudinal_reversal_overlay_zone(
        original,
        long_axis=long_axis,
        maximum_separation=maximum_separation,
        minimum_overlap_ratio=minimum_overlap_ratio,
    )
    if overlay_zone is None:
        return decision

    lost_ids = set(decision.lost_source_ids)
    lost_edges = [
        edge
        for edge in semantics.direct_edges
        if lost_ids.intersection(edge.source_ids)
    ]
    fidelity_band = candidate.boundary.buffer(
        fidelity_tolerance_mm,
        cap_style=3,
        join_style=2,
    )
    unpreserved_parts = [edge.line.difference(fidelity_band) for edge in lost_edges]
    if not lost_edges or any(
        part.difference(overlay_zone).length > max(fidelity_tolerance_mm, 1e-9)
        for part in unpreserved_parts
    ):
        return decision

    axis_span = (
        float(original.bounds[2] - original.bounds[0])
        if long_axis == "x"
        else float(original.bounds[3] - original.bounds[1])
    )
    # A real narrow notch interrupts the silhouette into separate source
    # edges.  A Tekla face/visibility overlay instead contributes one long
    # source course which continues on the accepted boundary on both sides of
    # the local U-turn.  Require that positive evidence before reclassification.
    if not any(
        edge.line.length >= minimum_continuing_course_ratio * axis_span
        and edge.line.intersection(fidelity_band).length
        >= minimum_continuing_course_ratio * axis_span
        for edge in lost_edges
    ):
        return decision

    reclassified = tuple(sorted(lost_ids))
    return BoundaryRepairDecision(
        polygon=candidate,
        applied=True,
        reason="longitudinal_projection_overlay_regularized",
        repair_kind=repair_kind,
        protected_source_ids=tuple(
            source_id
            for source_id in decision.protected_source_ids
            if source_id not in lost_ids
        ),
        lost_source_ids=(),
        fidelity_tolerance_mm=fidelity_tolerance_mm,
        reclassified_source_ids=reclassified,
    )


def assess_selected_projection_boundary(
    polygon: Polygon,
    entities: Iterable[DXFEntity],
    *,
    entity_source_ids: tuple[str, ...] = (),
    association_tolerance_mm: float = 0.15,
    fidelity_tolerance_mm: float = 1e-7,
) -> BoundaryRepairDecision:
    """Assess final selected geometry against nearby direct source edges."""

    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=entity_source_ids,
        association_tolerance_mm=association_tolerance_mm,
    )
    return evaluate_boundary_repair(
        polygon,
        polygon,
        semantics,
        fidelity_tolerance_mm=fidelity_tolerance_mm,
        repair_kind="selected_projection_boundary",
    )
