from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import atan2, cos, degrees, fsum, hypot, radians, sin

from .source_ir import SourceDocumentIR, SourceEntityIR

Point2 = tuple[float, float]


@dataclass(frozen=True, slots=True)
class ViewFrame:
    """A reversible, source-derived local frame for one Part projection."""

    origin: Point2
    longitudinal_axis: Point2
    transverse_axis: Point2
    longitudinal_min: float
    longitudinal_max: float
    transverse_min: float
    transverse_max: float

    def world_to_local(self, point: Point2) -> Point2:
        dx = point[0] - self.origin[0]
        dy = point[1] - self.origin[1]
        return (
            dx * self.longitudinal_axis[0] + dy * self.longitudinal_axis[1],
            dx * self.transverse_axis[0] + dy * self.transverse_axis[1],
        )

    def local_to_world(self, point: Point2) -> Point2:
        return (
            self.origin[0]
            + point[0] * self.longitudinal_axis[0]
            + point[1] * self.transverse_axis[0],
            self.origin[1]
            + point[0] * self.longitudinal_axis[1]
            + point[1] * self.transverse_axis[1],
        )

    @property
    def longitudinal_span(self) -> float:
        return self.longitudinal_max - self.longitudinal_min

    @property
    def transverse_span(self) -> float:
        return self.transverse_max - self.transverse_min

    @property
    def normalized_bounds(self) -> tuple[float, float, float, float]:
        return (0.0, 0.0, self.longitudinal_span, self.transverse_span)


@dataclass(frozen=True, slots=True)
class PartViewIR:
    group_id: str
    block_name: str
    entities: tuple[SourceEntityIR, ...]
    frame: ViewFrame


def _arc_endpoint(entity: SourceEntityIR, angle_degrees: float) -> Point2:
    assert entity.center is not None
    assert entity.radius is not None
    angle = radians(angle_degrees)
    return (
        entity.center[0] + entity.radius * cos(angle),
        entity.center[1] + entity.radius * sin(angle),
    )


def _entity_reference_points(entity: SourceEntityIR) -> tuple[Point2, ...]:
    if entity.kind == "LINE" and entity.start is not None and entity.end is not None:
        return (entity.start, entity.end)
    if (
        entity.kind == "ARC"
        and entity.center is not None
        and entity.radius is not None
        and entity.start_angle is not None
        and entity.end_angle is not None
    ):
        return (
            _arc_endpoint(entity, entity.start_angle),
            _arc_endpoint(entity, entity.end_angle),
        )
    if (
        entity.kind == "CIRCLE"
        and entity.center is not None
        and entity.radius is not None
    ):
        x, y = entity.center
        radius = entity.radius
        return ((x - radius, y), (x + radius, y), (x, y - radius), (x, y + radius))
    if entity.points:
        return tuple((point[0], point[1]) for point in entity.points)
    return ()


def _deduplicate_reference_points(
    points: tuple[Point2, ...],
    *,
    tolerance_mm: float = 1e-7,
) -> tuple[Point2, ...]:
    """Collapse numerically repeated endpoints without using a world grid.

    LINE and ARC entities often share one Tekla endpoint.  A rigid transform can
    leave their independently evaluated coordinates a few picometres apart, so
    exact ``set`` deduplication changes the centroid weighting.  Euclidean
    connected components are rotation/translation invariant and the tolerance
    remains four orders below the manufacturing topology grid.
    """

    ordered = tuple(sorted(points))
    parents = list(range(len(ordered)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[max(first_root, second_root)] = min(first_root, second_root)

    for index, point in enumerate(ordered):
        previous = index - 1
        while previous >= 0 and point[0] - ordered[previous][0] <= tolerance_mm:
            other = ordered[previous]
            if hypot(point[0] - other[0], point[1] - other[1]) <= tolerance_mm:
                union(index, previous)
            previous -= 1
    clusters: dict[int, list[Point2]] = {}
    for index, point in enumerate(ordered):
        clusters.setdefault(find(index), []).append(point)
    return tuple(
        sorted(
            (
                fsum(point[0] for point in cluster) / len(cluster),
                fsum(point[1] for point in cluster) / len(cluster),
            )
            for cluster in clusters.values()
        )
    )


def _dominant_line_axis(
    entities: tuple[SourceEntityIR, ...],
    *,
    nominal_length_mm: float | None = None,
) -> Point2:
    if nominal_length_mm is not None and nominal_length_mm <= 0.0:
        raise ValueError("nominal_length_mm must be positive")
    courses: list[tuple[float, float]] = []
    for entity in entities:
        if entity.kind != "LINE" or entity.start is None or entity.end is None:
            continue
        dx = entity.end[0] - entity.start[0]
        dy = entity.end[1] - entity.start[1]
        length = hypot(dx, dy)
        if length <= 1e-12:
            continue
        courses.append((atan2(dy, dx) % radians(180.0), length))
    if not courses:
        raise ValueError("Part view has no unique source-derived longitudinal axis")

    def distance_mod_pi(first: float, second: float) -> float:
        delta = abs(first - second) % radians(180.0)
        return min(delta, radians(180.0) - delta)

    # Use a source orientation family, not an average of unrelated end caps.
    # Averaging can rotate a horizontal member several degrees when it has many
    # oblique end cuts.  Tekla's main courses instead form a repeated, nearly
    # collinear family; total covered source length selects that family.
    family_tolerance = radians(0.05)
    points = tuple(
        point for entity in entities for point in _entity_reference_points(entity)
    )

    def spans(angle: float) -> tuple[float, float]:
        axis = (cos(angle), sin(angle))
        normal = (-axis[1], axis[0])
        longitudinal = tuple(
            point[0] * axis[0] + point[1] * axis[1] for point in points
        )
        transverse = tuple(
            point[0] * normal[0] + point[1] * normal[1] for point in points
        )
        longitudinal_span = max(longitudinal) - min(longitudinal)
        transverse_span = max(transverse) - min(transverse)
        return (longitudinal_span, transverse_span)

    def rank(candidate: tuple[float, float]) -> tuple[float, ...]:
        angle, candidate_length = candidate
        longitudinal_span, transverse_span = spans(angle)
        family_coverage = sum(
            length
            for course_angle, length in courses
            if distance_mod_pi(course_angle, angle) <= family_tolerance
        )
        geometric_rank = (
            -(longitudinal_span / max(transverse_span, 1e-12)),
            -family_coverage,
            -candidate_length,
            degrees(angle),
        )
        if nominal_length_mm is None:
            return geometric_rank
        # A member may legitimately be shorter than its BOX section is tall.
        # In that case aspect ratio is not a valid axis discriminator.  The
        # title-record length is source evidence for the member direction;
        # drawing scale is deliberately absent from this BOX axis decision.
        nominal_residual = abs(longitudinal_span - nominal_length_mm) / max(
            nominal_length_mm,
            1.0,
        )
        return (nominal_residual, *geometric_rank)

    ranked = sorted(
        courses,
        key=rank,
    )
    selected = ranked[0][0]
    family = tuple(
        (angle, length)
        for angle, length in courses
        if distance_mod_pi(angle, selected) <= family_tolerance
    )
    doubled_x = sum(length * cos(2.0 * angle) for angle, length in family)
    doubled_y = sum(length * sin(2.0 * angle) for angle, length in family)
    angle = 0.5 * atan2(doubled_y, doubled_x)
    axis = (cos(angle), sin(angle))
    if axis[0] < -1e-12 or (abs(axis[0]) <= 1e-12 and axis[1] < 0.0):
        axis = (-axis[0], -axis[1])
    return axis


def derive_view_frame(
    entities: Iterable[SourceEntityIR],
    *,
    nominal_length_mm: float | None = None,
) -> ViewFrame:
    """Derive the member-axis frame without relying on drawing placement."""

    materialized = tuple(
        entity for entity in entities if entity.layer.casefold() == "part"
    )
    if not materialized:
        raise ValueError("Part view frame requires Part-layer source entities")
    points = tuple(
        point for entity in materialized for point in _entity_reference_points(entity)
    )
    if len(points) < 2:
        raise ValueError("Part view frame requires at least two geometry points")
    longitudinal_axis = _dominant_line_axis(
        materialized,
        nominal_length_mm=nominal_length_mm,
    )
    transverse_axis = (-longitudinal_axis[1], longitudinal_axis[0])
    semantic_points = _deduplicate_reference_points(points)
    origin = (
        fsum(point[0] for point in semantic_points) / len(semantic_points),
        fsum(point[1] for point in semantic_points) / len(semantic_points),
    )
    longitudinal = tuple(
        (point[0] - origin[0]) * longitudinal_axis[0]
        + (point[1] - origin[1]) * longitudinal_axis[1]
        for point in points
    )
    transverse = tuple(
        (point[0] - origin[0]) * transverse_axis[0]
        + (point[1] - origin[1]) * transverse_axis[1]
        for point in points
    )
    longitudinal_min = min(longitudinal)
    longitudinal_max = max(longitudinal)
    transverse_min = min(transverse)
    transverse_max = max(transverse)
    return ViewFrame(
        origin=origin,
        longitudinal_axis=longitudinal_axis,
        transverse_axis=transverse_axis,
        longitudinal_min=longitudinal_min,
        longitudinal_max=longitudinal_max,
        transverse_min=transverse_min,
        transverse_max=transverse_max,
    )


def build_part_views(
    source: SourceDocumentIR,
    *,
    nominal_length_mm: float | None = None,
) -> tuple[PartViewIR, ...]:
    """Build one local Part view per Tekla Part object group."""

    views: list[PartViewIR] = []
    for group in source.groups_by_layer("Part"):
        entities = tuple(
            entity
            for entity in source.entities_for_group(group.group_id)
            if entity.layer.casefold() == "part"
        )
        views.append(
            PartViewIR(
                group_id=group.group_id,
                block_name=group.block_name,
                entities=entities,
                frame=derive_view_frame(
                    entities,
                    nominal_length_mm=nominal_length_mm,
                ),
            )
        )
    return tuple(views)
