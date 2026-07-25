from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from heapq import heappop, heappush
from math import ceil, cos, hypot, radians, sin

from shapely import set_precision
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import polygonize, substring, unary_union

from .course_graph import CourseEdge, CourseGraph, build_course_graph
from .source_ir import SourceEntityIR, is_hidden_projection_linetype
from .view_frame import Point2, ViewFrame


@dataclass(frozen=True, slots=True)
class ProjectionFaceCandidate:
    polygon: Polygon
    boundary_source_ids: tuple[str, ...]
    vertex_source_ids: tuple[str, ...]
    source_conserved: bool
    grid_size_mm: float
    rule_ids: tuple[str, ...] = ()

    @property
    def longitudinal_span(self) -> float:
        return float(self.polygon.bounds[2] - self.polygon.bounds[0])

    @property
    def transverse_span(self) -> float:
        return float(self.polygon.bounds[3] - self.polygon.bounds[1])


CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID = (
    "BOX.PROJECTION.CONNECTED_MAXIMAL_MATERIAL_FACE"
)


@dataclass(frozen=True, slots=True)
class SourceFaceUnionSearchResult:
    candidates: tuple[ProjectionFaceCandidate, ...]
    subset_search_complete: bool
    state_budget_exhausted: bool
    states_visited: int
    connected_maximal_candidate_count: int
    diagnostics: tuple[str, ...]


CoursePath = tuple[tuple[CourseEdge, bool], ...]


@dataclass(frozen=True, slots=True)
class ConnectedCourseCycleSearchResult:
    candidates: tuple[ProjectionFaceCandidate, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class _CoursePathSearchResult:
    paths: tuple[CoursePath, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class _ConnectorCoordinateSearchResult:
    options: tuple[tuple[Point2, ...], ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class _SourceCurve:
    source_id: str
    line: LineString
    endpoints: tuple[Point2, Point2]
    arc_center: Point2 | None = None
    arc_radius: float | None = None
    arc_start_angle: float | None = None
    arc_sweep: float | None = None


def _angle_in_sweep(angle: float, start: float, sweep: float, tolerance: float) -> bool:
    relative = (angle - start) % 360.0
    return relative <= sweep + tolerance or abs(relative - 360.0) <= tolerance


def _source_curves(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    include_hidden: bool,
    max_arc_step_degrees: float = 3.0,
) -> tuple[_SourceCurve, ...]:
    curves: list[_SourceCurve] = []
    for entity in entities:
        if entity.layer.casefold() != "part":
            continue
        hidden_projection = is_hidden_projection_linetype(entity.linetype)
        if not include_hidden and hidden_projection:
            continue
        if (
            entity.kind == "LINE"
            and entity.start is not None
            and entity.end is not None
        ):
            start = frame.world_to_local(entity.start)
            end = frame.world_to_local(entity.end)
            if hypot(end[0] - start[0], end[1] - start[1]) <= 1e-9:
                continue
            curves.append(
                _SourceCurve(
                    source_id=entity.source_id,
                    line=LineString((start, end)),
                    endpoints=(start, end),
                )
            )
            continue
        if (
            entity.kind == "ARC"
            and entity.center is not None
            and entity.radius is not None
            and entity.start_angle is not None
            and entity.end_angle is not None
        ):
            sweep = (entity.end_angle - entity.start_angle) % 360.0
            count = max(4, int(ceil(sweep / max_arc_step_degrees)))
            world_points = tuple(
                (
                    entity.center[0]
                    + entity.radius
                    * cos(radians(entity.start_angle + sweep * index / count)),
                    entity.center[1]
                    + entity.radius
                    * sin(radians(entity.start_angle + sweep * index / count)),
                )
                for index in range(count + 1)
            )
            local_points = tuple(frame.world_to_local(point) for point in world_points)
            curves.append(
                _SourceCurve(
                    source_id=entity.source_id,
                    line=LineString(local_points),
                    endpoints=(local_points[0], local_points[-1]),
                    arc_center=frame.world_to_local(entity.center),
                    arc_radius=entity.radius,
                    # Angles in the local frame differ by the frame rotation.
                    arc_start_angle=(
                        entity.start_angle - radians_to_degrees(frame.longitudinal_axis)
                    )
                    % 360.0,
                    arc_sweep=sweep,
                )
            )
    return tuple(curves)


def radians_to_degrees(axis: Point2) -> float:
    from math import atan2, degrees

    return degrees(atan2(axis[1], axis[0]))


def _is_faceted_copy_of_arc(line: _SourceCurve, arc: _SourceCurve) -> bool:
    """Return whether a short LINE is a lower-fidelity copy of an exact ARC.

    Tekla can export the same rounded edge as both a true hidden ARC and a
    visible chain of short chords.  Both remain source evidence, but feeding
    both representations into planar noding creates artificial sliver faces.
    """

    if line.arc_center is not None or arc.arc_center is None:
        return False
    assert arc.arc_radius is not None
    assert arc.arc_start_angle is not None
    assert arc.arc_sweep is not None
    radial_tolerance = max(0.05, min(0.5, arc.arc_radius * 0.015))
    angular_tolerance = radial_tolerance / max(arc.arc_radius, radial_tolerance) * 57.3
    from math import atan2, degrees

    for endpoint in line.endpoints:
        dx = endpoint[0] - arc.arc_center[0]
        dy = endpoint[1] - arc.arc_center[1]
        if abs(hypot(dx, dy) - arc.arc_radius) > radial_tolerance:
            return False
        angle = degrees(atan2(dy, dx)) % 360.0
        if not _angle_in_sweep(
            angle,
            arc.arc_start_angle,
            arc.arc_sweep,
            angular_tolerance,
        ):
            return False
    midpoint = (
        (line.endpoints[0][0] + line.endpoints[1][0]) / 2.0,
        (line.endpoints[0][1] + line.endpoints[1][1]) / 2.0,
    )
    chordal_deviation = arc.arc_radius - hypot(
        midpoint[0] - arc.arc_center[0],
        midpoint[1] - arc.arc_center[1],
    )
    return -radial_tolerance <= chordal_deviation <= radial_tolerance


def _topology_curves(curves: tuple[_SourceCurve, ...]) -> tuple[_SourceCurve, ...]:
    """Choose one highest-fidelity representation for planar topology only."""

    arcs = tuple(curve for curve in curves if curve.arc_center is not None)
    retained: list[_SourceCurve] = []
    seen_arcs: set[tuple[int, ...]] = set()
    for curve in curves:
        if curve.arc_center is None:
            if any(_is_faceted_copy_of_arc(curve, arc) for arc in arcs):
                continue
            retained.append(curve)
            continue
        assert curve.arc_radius is not None
        assert curve.arc_start_angle is not None
        assert curve.arc_sweep is not None
        key = (
            round(curve.arc_center[0] / 0.01),
            round(curve.arc_center[1] / 0.01),
            round(curve.arc_radius / 0.01),
            round(curve.arc_start_angle / 0.01),
            round(curve.arc_sweep / 0.01),
        )
        if key in seen_arcs:
            continue
        seen_arcs.add(key)
        retained.append(curve)
    return tuple(retained)


def polygonize_part_projection(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    include_hidden: bool,
    grid_size_mm: float = 0.001,
) -> tuple[Polygon, ...]:
    """Node and polygonize Part source curves without assigning plate roles."""

    if grid_size_mm <= 0:
        raise ValueError("grid_size_mm must be positive")
    curves = _source_curves(entities, frame, include_hidden=include_hidden)
    if not curves:
        return ()
    topology_curves = _topology_curves(curves)
    noded = unary_union(
        set_precision(
            MultiLineString([curve.line for curve in topology_curves]),
            grid_size_mm,
            mode="valid_output",
        )
    )
    minimum_area = grid_size_mm * grid_size_mm
    return tuple(
        face
        for face in polygonize(noded)
        if isinstance(face, Polygon) and face.area > minimum_area
    )


def _vertex_authority(
    point: Point2,
    curves: tuple[_SourceCurve, ...],
    *,
    tolerance: float,
) -> tuple[str, ...]:
    direct = {
        curve.source_id
        for curve in curves
        if min(
            hypot(point[0] - endpoint[0], point[1] - endpoint[1])
            for endpoint in curve.endpoints
        )
        <= tolerance
    }
    for curve in curves:
        if (
            curve.arc_center is None
            or curve.arc_radius is None
            or curve.arc_start_angle is None
            or curve.arc_sweep is None
        ):
            continue
        dx = point[0] - curve.arc_center[0]
        dy = point[1] - curve.arc_center[1]
        radius = hypot(dx, dy)
        if abs(radius - curve.arc_radius) > tolerance:
            continue
        from math import atan2, degrees

        angle = degrees(atan2(dy, dx)) % 360.0
        angular_tolerance = max(0.01, tolerance / max(radius, tolerance) * 57.3)
        if _angle_in_sweep(
            angle,
            curve.arc_start_angle,
            curve.arc_sweep,
            angular_tolerance,
        ):
            direct.add(curve.source_id)
    return tuple(sorted(direct))


def _boundary_sources(
    polygon: Polygon,
    curves: tuple[_SourceCurve, ...],
    *,
    tolerance: float,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            curve.source_id
            for curve in curves
            # A transverse projection line only crosses the candidate boundary
            # at isolated points; buffer intersection would give those points
            # artificial length and falsely claim boundary authority.
            if curve.line.intersection(polygon.boundary).length
            >= min(curve.line.length, max(tolerance, 0.01))
        )
    )


def _assess_candidate(
    polygon: Polygon,
    curves: tuple[_SourceCurve, ...],
    *,
    grid_size_mm: float,
    endpoint_tolerance_mm: float,
) -> ProjectionFaceCandidate | None:
    simplified = polygon.simplify(
        max(0.01, grid_size_mm * 2.0),
        preserve_topology=True,
    )
    if not isinstance(simplified, Polygon) or not simplified.is_valid:
        return None
    # Part projection overlays can leave tiny enclosed faces after noding.
    # They are drawing topology, not through-cuts in a physical plate; actual
    # holes are lowered independently from Bolt/opening evidence.
    if simplified.interiors:
        return None
    vertex_sources: set[str] = set()
    for point in tuple(simplified.exterior.coords)[:-1]:
        sources = _vertex_authority(
            (float(point[0]), float(point[1])),
            curves,
            tolerance=endpoint_tolerance_mm,
        )
        if not sources:
            return None
        vertex_sources.update(sources)
    return ProjectionFaceCandidate(
        polygon=simplified,
        boundary_source_ids=_boundary_sources(
            simplified,
            curves,
            tolerance=max(endpoint_tolerance_mm, grid_size_mm * 2.0),
        ),
        vertex_source_ids=tuple(sorted(vertex_sources)),
        source_conserved=True,
        grid_size_mm=grid_size_mm,
    )


def _candidate_bucket(
    candidate: ProjectionFaceCandidate,
    *,
    target_transverse_mm: float,
) -> tuple[float, ...]:
    """Coarsely group numerical face partitions without O(n²) geometry calls."""

    bounds = tuple(
        round(float(value) / 0.05) * 0.05 for value in candidate.polygon.bounds
    )
    # Arc overlays can partition the same source envelope into hundreds of
    # sub-grid variants.  Their area drift is sub-millimetric, while genuinely
    # distinct overlapping physical faces differ by orders of magnitude more.
    area_quantum = max(1.0, target_transverse_mm * 0.1)
    area_bucket = round(candidate.polygon.area / area_quantum) * area_quantum
    return (*bounds, area_bucket)


def _polygon_members(geometry: object) -> tuple[Polygon, ...]:
    if isinstance(geometry, Polygon):
        return (geometry,)
    members = getattr(geometry, "geoms", ())
    return tuple(member for member in members if isinstance(member, Polygon))


def _assess_inner_band_candidate(
    polygon: Polygon,
    curves: tuple[_SourceCurve, ...],
    visible_curves: tuple[_SourceCurve, ...],
    *,
    normal: Point2,
    support_courses: tuple[tuple[float, str], tuple[float, str]],
    grid_size_mm: float,
    endpoint_tolerance_mm: float,
) -> ProjectionFaceCandidate | None:
    """Audit the guarded extension of verified BOX inner courses."""

    simplified = polygon.simplify(
        max(0.01, grid_size_mm * 2.0),
        preserve_topology=True,
    )
    if (
        not isinstance(simplified, Polygon)
        or not simplified.is_valid
        or simplified.interiors
    ):
        return None
    vertex_sources: set[str] = set()
    extension_tolerance = max(endpoint_tolerance_mm, grid_size_mm * 2.0)

    def independent_visible_crossing(
        candidates: tuple[_SourceCurve, ...],
        at: Point2,
    ) -> bool:
        directions: list[Point2] = []
        location = Point(at)
        for curve in candidates:
            along = curve.line.project(location)
            delta = min(0.1, max(curve.line.length * 0.01, 0.001))
            before = curve.line.interpolate(max(0.0, along - delta))
            after = curve.line.interpolate(min(curve.line.length, along + delta))
            dx = float(after.x - before.x)
            dy = float(after.y - before.y)
            length = hypot(dx, dy)
            if length > 1e-12:
                directions.append((dx / length, dy / length))
        return any(
            abs(first[0] * second[1] - first[1] * second[0]) > 1e-6
            for first_index, first in enumerate(directions)
            for second in directions[first_index + 1 :]
        )

    for coordinate in tuple(simplified.exterior.coords)[:-1]:
        point = (float(coordinate[0]), float(coordinate[1]))
        direct = _vertex_authority(
            point,
            curves,
            tolerance=endpoint_tolerance_mm,
        )
        if direct:
            vertex_sources.update(direct)
            continue
        point_offset = point[0] * normal[0] + point[1] * normal[1]
        support_ids = {
            source_id
            for offset, source_id in support_courses
            if abs(point_offset - offset) <= extension_tolerance
        }
        supporting_visible = tuple(
            curve
            for curve in visible_curves
            if curve.line.distance(Point(point)) <= extension_tolerance
        )
        visible_ids = {curve.source_id for curve in supporting_visible}
        if not (
            (support_ids and visible_ids)
            or independent_visible_crossing(supporting_visible, point)
        ):
            return None
        vertex_sources.update(support_ids)
        vertex_sources.update(visible_ids)
    boundary_sources = set(
        _boundary_sources(
            simplified,
            curves,
            tolerance=extension_tolerance,
        )
    )
    boundary_sources.update(source_id for _, source_id in support_courses)
    return ProjectionFaceCandidate(
        polygon=simplified,
        boundary_source_ids=tuple(sorted(boundary_sources)),
        vertex_source_ids=tuple(sorted(vertex_sources)),
        source_conserved=True,
        grid_size_mm=grid_size_mm,
    )


def enumerate_straight_inner_band_faces(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    target_transverse_mm: float,
    grid_size_mm: float = 0.001,
    parallel_tolerance_degrees: float = 0.1,
    distance_tolerance_mm: float | None = None,
    endpoint_tolerance_mm: float = 0.05,
) -> tuple[ProjectionFaceCandidate, ...]:
    """Clip the visible projection by paired straight inner flange courses.

    In Tekla's BOX projection, hidden longitudinal lines at distance
    ``H - 2*tf`` are the two inner flange surfaces.  They are open drawing
    courses; their endpoints meet the visible end outline, which supplies the
    physical web end caps.
    """

    if target_transverse_mm <= 0:
        raise ValueError("target_transverse_mm must be positive")
    materialized = tuple(entities)
    all_curves = _source_curves(materialized, frame, include_hidden=True)
    visible_curves = _source_curves(materialized, frame, include_hidden=False)
    visible_faces = polygonize_part_projection(
        materialized,
        frame,
        include_hidden=False,
        grid_size_mm=grid_size_mm,
    )
    if not visible_faces:
        return ()
    visible_region = unary_union(visible_faces)
    hidden_courses: list[tuple[Point2, Point2, str]] = []
    for entity in materialized:
        if (
            entity.layer.casefold() != "part"
            or not is_hidden_projection_linetype(entity.linetype)
            or entity.kind != "LINE"
            or entity.start is None
            or entity.end is None
        ):
            continue
        start = frame.world_to_local(entity.start)
        end = frame.world_to_local(entity.end)
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = hypot(dx, dy)
        if length <= 1e-9 or abs(dx) / length < cos(radians(15.0)):
            continue
        hidden_courses.append((start, end, entity.source_id))
    tolerance = (
        distance_tolerance_mm
        if distance_tolerance_mm is not None
        else max(0.20, target_transverse_mm * 0.0015)
    )
    maximum_sine = sin(radians(parallel_tolerance_degrees))
    bounds = visible_region.bounds
    extent = max(
        1.0,
        hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]) * 2.0,
    )
    accepted_by_bucket: dict[tuple[float, ...], ProjectionFaceCandidate] = {}
    for first_index, first in enumerate(hidden_courses):
        first_dx = first[1][0] - first[0][0]
        first_dy = first[1][1] - first[0][1]
        first_length = hypot(first_dx, first_dy)
        direction = (first_dx / first_length, first_dy / first_length)
        normal = (-direction[1], direction[0])
        first_offset = first[0][0] * normal[0] + first[0][1] * normal[1]
        first_interval = sorted(
            point[0] * direction[0] + point[1] * direction[1] for point in first[:2]
        )
        for second in hidden_courses[first_index + 1 :]:
            second_dx = second[1][0] - second[0][0]
            second_dy = second[1][1] - second[0][1]
            second_length = hypot(second_dx, second_dy)
            cross = abs(
                direction[0] * second_dy / second_length
                - direction[1] * second_dx / second_length
            )
            if cross > maximum_sine:
                continue
            second_offset = second[0][0] * normal[0] + second[0][1] * normal[1]
            if (
                abs(abs(second_offset - first_offset) - target_transverse_mm)
                > tolerance
            ):
                continue
            second_interval = sorted(
                point[0] * direction[0] + point[1] * direction[1]
                for point in second[:2]
            )
            overlap = min(first_interval[1], second_interval[1]) - max(
                first_interval[0], second_interval[0]
            )
            if overlap <= max(1.0, min(first_length, second_length) * 0.05):
                continue
            lower, upper = sorted((first_offset, second_offset))
            longitudinal = (
                min(first_interval[0], second_interval[0]) - extent,
                max(first_interval[1], second_interval[1]) + extent,
            )

            def point(
                along: float,
                across: float,
                axis: Point2 = direction,
                transverse_axis: Point2 = normal,
            ) -> Point2:
                return (
                    along * axis[0] + across * transverse_axis[0],
                    along * axis[1] + across * transverse_axis[1],
                )

            slab = Polygon(
                (
                    point(longitudinal[0], lower),
                    point(longitudinal[1], lower),
                    point(longitudinal[1], upper),
                    point(longitudinal[0], upper),
                )
            )
            clipped = visible_region.intersection(slab)
            for polygon in _polygon_members(clipped):
                candidate = _assess_inner_band_candidate(
                    polygon,
                    all_curves,
                    visible_curves,
                    normal=normal,
                    support_courses=(
                        (first_offset, first[2]),
                        (second_offset, second[2]),
                    ),
                    grid_size_mm=grid_size_mm,
                    endpoint_tolerance_mm=endpoint_tolerance_mm,
                )
                if candidate is None:
                    continue
                bucket = _candidate_bucket(
                    candidate,
                    target_transverse_mm=target_transverse_mm,
                )
                existing = accepted_by_bucket.get(bucket)
                if existing is None or candidate.polygon.area > existing.polygon.area:
                    accepted_by_bucket[bucket] = candidate
    accepted = list(accepted_by_bucket.values())
    accepted.sort(
        key=lambda candidate: (
            -candidate.polygon.area,
            candidate.polygon.bounds,
            candidate.boundary_source_ids,
        )
    )
    return tuple(accepted)


def _course_edge_length(edge: CourseEdge) -> float:
    if edge.kind == "ARC" and edge.radius is not None:
        assert edge.start_angle is not None
        assert edge.end_angle is not None
        return edge.radius * radians((edge.end_angle - edge.start_angle) % 360.0)
    return hypot(edge.end[0] - edge.start[0], edge.end[1] - edge.start[1])


def _shortest_course_path(
    graph: CourseGraph,
    start_node: str,
    end_node: str,
    *,
    excluded_source_ids: frozenset[str],
    maximum_length_mm: float,
) -> tuple[tuple[CourseEdge, bool], ...] | None:
    adjacency: dict[str, list[tuple[CourseEdge, bool, str]]] = {}
    for edge in graph.edges:
        if any(source_id in excluded_source_ids for source_id in edge.source_ids):
            continue
        adjacency.setdefault(edge.start_node, []).append((edge, True, edge.end_node))
        adjacency.setdefault(edge.end_node, []).append((edge, False, edge.start_node))
    queue: list[tuple[float, int, str, tuple[tuple[CourseEdge, bool], ...]]] = [
        (0.0, 0, start_node, ())
    ]
    best: dict[str, float] = {start_node: 0.0}
    while queue:
        distance, hops, node, path = heappop(queue)
        if node == end_node:
            return path
        if distance > best.get(node, float("inf")) + 1e-9:
            continue
        for edge, forward, neighbor in adjacency.get(node, ()):
            candidate_distance = distance + _course_edge_length(edge)
            if candidate_distance > maximum_length_mm:
                continue
            if candidate_distance + 1e-9 >= best.get(neighbor, float("inf")):
                continue
            best[neighbor] = candidate_distance
            heappush(
                queue,
                (candidate_distance, hops + 1, neighbor, (*path, (edge, forward))),
            )
    return None


def _path_coordinates(
    path: tuple[tuple[CourseEdge, bool], ...],
    curves_by_source_id: dict[str, _SourceCurve],
) -> tuple[Point2, ...]:
    result: list[Point2] = []
    for edge, forward in path:
        curve = curves_by_source_id[edge.source_ids[0]]
        coordinates = tuple(
            (float(point[0]), float(point[1])) for point in curve.line.coords
        )
        desired_start = edge.start if forward else edge.end
        if hypot(
            coordinates[-1][0] - desired_start[0],
            coordinates[-1][1] - desired_start[1],
        ) < hypot(
            coordinates[0][0] - desired_start[0],
            coordinates[0][1] - desired_start[1],
        ):
            coordinates = tuple(reversed(coordinates))
        if (
            result
            and hypot(
                result[-1][0] - coordinates[0][0],
                result[-1][1] - coordinates[0][1],
            )
            <= 0.15
        ):
            result.extend(coordinates[1:])
        else:
            result.extend(coordinates)
    return tuple(result)


def _connector_coordinate_options(
    graph: CourseGraph,
    curves_by_source_id: dict[str, _SourceCurve],
    visible_curves: tuple[_SourceCurve, ...],
    start: tuple[Point2, str],
    end: tuple[Point2, str],
    *,
    excluded_source_ids: frozenset[str],
    maximum_length_mm: float,
    endpoint_tolerance_mm: float,
    maximum_paths: int,
    maximum_path_expansions: int,
) -> _ConnectorCoordinateSearchResult:
    """Enumerate a bounded set of physical end-cap interpretations.

    A Tekla BOX end can contain both an inner projection station and the real
    outer plate edge.  Choosing only the graph-shortest connector commits to
    the station before the whole plate is assembled and shortens the result.
    Keep the local alternatives in the projection IR; the later assembly
    ranking can then select a globally coherent four-plate interpretation.
    """

    excluded_edge_ids = frozenset(
        edge.edge_id
        for edge in graph.edges
        if any(source_id in excluded_source_ids for source_id in edge.source_ids)
    )
    path_search = _k_shortest_simple_course_paths(
        graph,
        start[1],
        end[1],
        maximum_paths=maximum_paths,
        maximum_length_mm=maximum_length_mm,
        maximum_expansions=maximum_path_expansions,
        excluded_edge_ids=excluded_edge_ids,
    )
    options: list[tuple[Point2, ...]] = []
    for path in path_search.paths:
        coordinates = _path_coordinates(path, curves_by_source_id)
        if not coordinates:
            continue
        options.append((start[0], *coordinates[1:-1], end[0]))

    # An endpoint may land in the interior of one long visible edge.  The
    # endpoint graph deliberately does not node arbitrary crossings, so retain
    # the exact substring as another source-backed connector option.
    start_point = Point(start[0])
    end_point = Point(end[0])
    for curve in visible_curves:
        if (
            curve.source_id in excluded_source_ids
            or curve.line.distance(start_point) > endpoint_tolerance_mm
            or curve.line.distance(end_point) > endpoint_tolerance_mm
        ):
            continue
        start_distance = curve.line.project(start_point)
        end_distance = curve.line.project(end_point)
        connector = substring(curve.line, start_distance, end_distance)
        if connector.length > maximum_length_mm:
            continue
        coordinates = tuple(
            (float(point[0]), float(point[1])) for point in connector.coords
        )
        if coordinates:
            options.append((start[0], *coordinates[1:-1], end[0]))

    unique: dict[tuple[tuple[int, int], ...], tuple[Point2, ...]] = {}
    for coordinates in options:
        key = tuple(
            (round(point[0] / 0.001), round(point[1] / 0.001)) for point in coordinates
        )
        unique.setdefault(key, coordinates)
    return _ConnectorCoordinateSearchResult(
        options=tuple(unique.values()),
        complete=path_search.complete,
    )


def search_connected_inner_course_cycles(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    target_transverse_mm: float,
    endpoint_tolerance_mm: float = 0.15,
    grid_size_mm: float = 0.001,
    maximum_paths_per_connector: int = 64,
    maximum_path_expansions: int = 50_000,
) -> ConnectedCourseCycleSearchResult:
    """Close paired inner courses through bounded real source end paths."""

    if target_transverse_mm <= 0:
        raise ValueError("target_transverse_mm must be positive")
    materialized = tuple(entities)
    curves = _source_curves(materialized, frame, include_hidden=True)
    visible_curves = _source_curves(materialized, frame, include_hidden=False)
    curves_by_source_id = {curve.source_id: curve for curve in curves}
    graph = build_course_graph(
        materialized,
        frame,
        endpoint_tolerance_mm=endpoint_tolerance_mm,
    )
    hidden_courses: list[tuple[Point2, Point2, str]] = []
    for entity in materialized:
        if (
            entity.layer.casefold() != "part"
            or not is_hidden_projection_linetype(entity.linetype)
            or entity.kind != "LINE"
            or entity.start is None
            or entity.end is None
        ):
            continue
        start = frame.world_to_local(entity.start)
        end = frame.world_to_local(entity.end)
        length = hypot(end[0] - start[0], end[1] - start[1])
        if length > 1e-9 and abs(end[0] - start[0]) / length >= cos(radians(15.0)):
            hidden_courses.append((start, end, entity.source_id))
    distance_tolerance = max(0.20, target_transverse_mm * 0.0015)
    accepted_by_bucket: dict[tuple[float, ...], ProjectionFaceCandidate] = {}
    complete = True
    for first_index, first in enumerate(hidden_courses):
        dx = first[1][0] - first[0][0]
        dy = first[1][1] - first[0][1]
        length = hypot(dx, dy)
        direction = (dx / length, dy / length)
        normal = (-direction[1], direction[0])
        first_offset = first[0][0] * normal[0] + first[0][1] * normal[1]
        for second in hidden_courses[first_index + 1 :]:
            second_dx = second[1][0] - second[0][0]
            second_dy = second[1][1] - second[0][1]
            second_length = hypot(second_dx, second_dy)
            cross = abs(
                direction[0] * second_dy / second_length
                - direction[1] * second_dx / second_length
            )
            if cross > sin(radians(0.1)):
                continue
            second_offset = second[0][0] * normal[0] + second[0][1] * normal[1]
            if (
                abs(abs(second_offset - first_offset) - target_transverse_mm)
                > distance_tolerance
            ):
                continue
            first_edge = graph.edge_by_source_id(first[2])
            second_edge = graph.edge_by_source_id(second[2])

            def sides(
                _course: tuple[Point2, Point2, str],
                edge: CourseEdge,
                axis: Point2 = direction,
            ) -> tuple[tuple[Point2, str], tuple[Point2, str]]:
                endpoints = (
                    (edge.start, edge.start_node),
                    (edge.end, edge.end_node),
                )
                return tuple(
                    sorted(
                        endpoints,
                        key=lambda item: item[0][0] * axis[0] + item[0][1] * axis[1],
                    )
                )  # type: ignore[return-value]

            first_left, first_right = sides(first, first_edge)
            second_left, second_right = sides(second, second_edge)
            excluded = frozenset((first[2], second[2]))
            maximum_connector = target_transverse_mm * 8.0
            right_options = _connector_coordinate_options(
                graph,
                curves_by_source_id,
                visible_curves,
                first_right,
                second_right,
                excluded_source_ids=excluded,
                maximum_length_mm=maximum_connector,
                endpoint_tolerance_mm=endpoint_tolerance_mm,
                maximum_paths=maximum_paths_per_connector,
                maximum_path_expansions=maximum_path_expansions,
            )
            left_options = _connector_coordinate_options(
                graph,
                curves_by_source_id,
                visible_curves,
                second_left,
                first_left,
                excluded_source_ids=excluded,
                maximum_length_mm=maximum_connector,
                endpoint_tolerance_mm=endpoint_tolerance_mm,
                maximum_paths=maximum_paths_per_connector,
                maximum_path_expansions=maximum_path_expansions,
            )
            complete = complete and right_options.complete and left_options.complete
            if not right_options.options or not left_options.options:
                continue
            for right_coordinates in right_options.options:
                for left_coordinates in left_options.options:
                    coordinates = (
                        first_left[0],
                        first_right[0],
                        *right_coordinates[1:],
                        second_left[0],
                        *left_coordinates[1:],
                    )
                    polygon = Polygon(coordinates)
                    if not polygon.is_valid:
                        polygon = polygon.buffer(0)
                    if not isinstance(polygon, Polygon):
                        continue
                    candidate = _assess_candidate(
                        polygon,
                        curves,
                        grid_size_mm=grid_size_mm,
                        endpoint_tolerance_mm=endpoint_tolerance_mm,
                    )
                    if candidate is None:
                        continue
                    bucket = _candidate_bucket(
                        candidate,
                        target_transverse_mm=target_transverse_mm,
                    )
                    existing = accepted_by_bucket.get(bucket)
                    if (
                        existing is None
                        or candidate.polygon.area > existing.polygon.area
                    ):
                        accepted_by_bucket[bucket] = candidate
    accepted = list(accepted_by_bucket.values())
    accepted.sort(
        key=lambda candidate: (
            -candidate.polygon.area,
            candidate.polygon.bounds,
            candidate.boundary_source_ids,
        )
    )
    return ConnectedCourseCycleSearchResult(
        candidates=tuple(accepted),
        complete=complete,
    )


def enumerate_connected_inner_course_cycles(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    target_transverse_mm: float,
    endpoint_tolerance_mm: float = 0.15,
    grid_size_mm: float = 0.001,
    maximum_paths_per_connector: int = 8,
) -> tuple[ProjectionFaceCandidate, ...]:
    """Compatibility wrapper returning connected course candidates only."""

    return search_connected_inner_course_cycles(
        entities,
        frame,
        target_transverse_mm=target_transverse_mm,
        endpoint_tolerance_mm=endpoint_tolerance_mm,
        grid_size_mm=grid_size_mm,
        maximum_paths_per_connector=maximum_paths_per_connector,
    ).candidates


def _k_shortest_simple_course_paths(
    graph: CourseGraph,
    start_node: str,
    end_node: str,
    *,
    maximum_paths: int,
    maximum_length_mm: float,
    maximum_expansions: int = 50_000,
    excluded_edge_ids: frozenset[str] = frozenset(),
) -> _CoursePathSearchResult:
    # Collapse exact duplicate visible/hidden representations between the same
    # graph nodes; they are evidence alternatives, not different paths.
    unique: dict[tuple[object, ...], CourseEdge] = {}
    for edge in graph.edges:
        if edge.edge_id in excluded_edge_ids:
            continue
        canonical_start, canonical_end = sorted(
            (
                (round(edge.start[0] / 0.001), round(edge.start[1] / 0.001)),
                (round(edge.end[0] / 0.001), round(edge.end[1] / 0.001)),
            )
        )
        key: tuple[object, ...] = (
            edge.kind,
            min(edge.start_node, edge.end_node),
            max(edge.start_node, edge.end_node),
            canonical_start,
            canonical_end,
        )
        if edge.kind == "ARC":
            assert edge.center is not None
            assert edge.radius is not None
            assert edge.start_angle is not None
            assert edge.end_angle is not None
            key = (
                *key,
                (
                    round(edge.center[0] / 0.001),
                    round(edge.center[1] / 0.001),
                ),
                round(edge.radius / 0.001),
                round(edge.start_angle % 360.0, 9),
                round((edge.end_angle - edge.start_angle) % 360.0, 9),
            )
        existing = unique.get(key)
        if existing is None:
            unique[key] = edge
            continue
        unique[key] = replace(
            existing,
            visible=existing.visible or edge.visible,
            source_ids=tuple(sorted(set(existing.source_ids) | set(edge.source_ids))),
        )
    adjacency: dict[str, list[tuple[CourseEdge, bool, str]]] = {}
    for edge in unique.values():
        adjacency.setdefault(edge.start_node, []).append((edge, True, edge.end_node))
        adjacency.setdefault(edge.end_node, []).append((edge, False, edge.start_node))
    queue: list[
        tuple[
            float,
            int,
            str,
            frozenset[str],
            tuple[tuple[CourseEdge, bool], ...],
        ]
    ] = [(0.0, 0, start_node, frozenset((start_node,)), ())]
    serial = 0
    expansions = 0
    completed: list[CoursePath] = []
    seen_signatures: set[tuple[tuple[str, bool], ...]] = set()
    while queue and len(completed) < maximum_paths and expansions < maximum_expansions:
        distance, _serial, node, visited, path = heappop(queue)
        expansions += 1
        if node == end_node:
            signature = tuple((edge.edge_id, forward) for edge, forward in path)
            if signature not in seen_signatures:
                seen_signatures.add(signature)
                completed.append(path)
            continue
        for edge, forward, neighbor in adjacency.get(node, ()):
            if neighbor in visited:
                continue
            candidate_distance = distance + _course_edge_length(edge)
            if candidate_distance > maximum_length_mm:
                continue
            serial += 1
            heappush(
                queue,
                (
                    candidate_distance,
                    serial,
                    neighbor,
                    visited | {neighbor},
                    (*path, (edge, forward)),
                ),
            )
    return _CoursePathSearchResult(
        paths=tuple(completed),
        complete=not queue,
    )


def enumerate_endpoint_cap_path_cycles(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    target_transverse_mm: float,
    endpoint_tolerance_mm: float = 0.15,
    maximum_paths_per_cap: int = 16,
) -> tuple[ProjectionFaceCandidate, ...]:
    """Close long-course endpoint pairs through multiple source path alternatives."""

    if target_transverse_mm <= 0:
        raise ValueError("target_transverse_mm must be positive")
    materialized = tuple(
        entity
        for entity in entities
        if entity.layer.casefold() == "part" and entity.kind in {"LINE", "ARC"}
    )
    base_graph = build_course_graph(
        materialized,
        frame,
        endpoint_tolerance_mm=endpoint_tolerance_mm,
    )
    base_points = {node.node_id: node.point for node in base_graph.nodes}
    base_degree: dict[str, int] = {node.node_id: 0 for node in base_graph.nodes}
    base_incident: dict[str, list[CourseEdge]] = {
        node.node_id: [] for node in base_graph.nodes
    }
    existing_pairs: set[frozenset[str]] = set()
    for edge in base_graph.edges:
        base_degree[edge.start_node] += 1
        base_degree[edge.end_node] += 1
        base_incident[edge.start_node].append(edge)
        base_incident[edge.end_node].append(edge)
        existing_pairs.add(frozenset((edge.start_node, edge.end_node)))
    synthetic: list[SourceEntityIR] = []
    node_ids = tuple(sorted(base_points))
    for first_index, first_node in enumerate(node_ids):
        for second_node in node_ids[first_index + 1 :]:
            if frozenset((first_node, second_node)) in existing_pairs:
                continue
            degrees = (base_degree[first_node], base_degree[second_node])
            if min(degrees) != 1 or max(degrees) > 2:
                continue
            first = base_points[first_node]
            second = base_points[second_node]
            dx = second[0] - first[0]
            dy = second[1] - first[1]
            distance = hypot(dx, dy)
            if not (endpoint_tolerance_mm < distance <= target_transverse_mm * 0.20):
                continue
            degree_one_node = (
                first_node if base_degree[first_node] == 1 else second_node
            )
            direction = (dx / distance, dy / distance)
            if degree_one_node == second_node:
                direction = (-direction[0], -direction[1])
            aligned = False
            for edge in base_incident[degree_one_node]:
                other = edge.end if edge.start_node == degree_one_node else edge.start
                origin = base_points[degree_one_node]
                edge_dx = other[0] - origin[0]
                edge_dy = other[1] - origin[1]
                edge_length = hypot(edge_dx, edge_dy)
                if edge_length >= target_transverse_mm * 0.10 and abs(
                    direction[0] * edge_dx / edge_length
                    + direction[1] * edge_dy / edge_length
                ) >= cos(radians(10.0)):
                    aligned = True
                    break
            if not aligned:
                continue
            synthetic.append(
                SourceEntityIR(
                    source_id=f"inferred:micro-gap:{first_node}:{second_node}",
                    group_id="inferred:micro-gap",
                    handle=f"{first_node}:{second_node}",
                    kind="LINE",
                    layer="Part",
                    linetype="XKITLINE04",
                    start=frame.local_to_world(first),
                    end=frame.local_to_world(second),
                )
            )
    augmented = (*materialized, *synthetic)
    graph = build_course_graph(
        augmented,
        frame,
        endpoint_tolerance_mm=endpoint_tolerance_mm,
    )
    curves = _source_curves(augmented, frame, include_hidden=True)
    curves_by_source_id = {curve.source_id: curve for curve in curves}
    long_course_threshold = frame.longitudinal_span * 0.40
    cap_nodes = {
        node_id
        for edge in graph.edges
        if _course_edge_length(edge) >= long_course_threshold
        for node_id in (edge.start_node, edge.end_node)
    }
    node_points = {node.node_id: node.point for node in graph.nodes}
    accepted_by_bucket: dict[tuple[float, ...], ProjectionFaceCandidate] = {}
    ordered_nodes = tuple(sorted(cap_nodes))
    for first_index, first_node in enumerate(ordered_nodes):
        for second_node in ordered_nodes[first_index + 1 :]:
            first = node_points[first_node]
            second = node_points[second_node]
            cap_length = hypot(second[0] - first[0], second[1] - first[1])
            if not (
                target_transverse_mm * 0.75 <= cap_length <= target_transverse_mm * 1.65
            ):
                continue
            path_search = _k_shortest_simple_course_paths(
                graph,
                first_node,
                second_node,
                maximum_paths=maximum_paths_per_cap,
                maximum_length_mm=frame.longitudinal_span * 4.0
                + target_transverse_mm * 4.0,
                excluded_edge_ids=frozenset(
                    edge.edge_id
                    for edge in graph.edges
                    if {edge.start_node, edge.end_node} == {first_node, second_node}
                ),
            )
            for path in path_search.paths:
                coordinates = _path_coordinates(path, curves_by_source_id)
                polygon = Polygon((*coordinates, first))
                if not polygon.is_valid:
                    repaired_members = sorted(
                        _polygon_members(polygon.buffer(0)),
                        key=lambda item: item.area,
                        reverse=True,
                    )
                    if (
                        not repaired_members
                        or sum(item.area for item in repaired_members[1:])
                        > repaired_members[0].area * 1e-6
                    ):
                        continue
                    polygon = repaired_members[0]
                if polygon.area <= target_transverse_mm**2:
                    continue
                effective_width = (
                    2.0
                    * polygon.area
                    / max(polygon.length - 2.0 * target_transverse_mm, 1e-9)
                )
                if (
                    abs(effective_width - target_transverse_mm)
                    > target_transverse_mm * 0.03
                ):
                    continue
                candidate = _assess_candidate(
                    polygon,
                    curves,
                    grid_size_mm=0.001,
                    endpoint_tolerance_mm=endpoint_tolerance_mm,
                )
                if candidate is None:
                    continue
                bucket = _candidate_bucket(
                    candidate,
                    target_transverse_mm=target_transverse_mm,
                )
                existing = accepted_by_bucket.get(bucket)
                if existing is None or candidate.polygon.area > existing.polygon.area:
                    accepted_by_bucket[bucket] = candidate
    accepted = list(accepted_by_bucket.values())
    accepted.sort(
        key=lambda candidate: (
            -candidate.polygon.area,
            candidate.polygon.bounds,
            candidate.boundary_source_ids,
        )
    )
    return tuple(accepted)


@dataclass(frozen=True, slots=True)
class _VirtualCycleEdge:
    edge_id: str
    start_node: str
    end_node: str
    source_id: str | None
    virtual: bool


def enumerate_projection_course_virtual_cycles(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    target_transverse_mm: float,
    endpoint_tolerance_mm: float = 0.15,
    maximum_virtual_edges: int = 3,
    maximum_cycles: int = 5_000,
    maximum_candidates: int = 64,
    effective_width_tolerance_fraction: float = 0.02,
) -> tuple[ProjectionFaceCandidate, ...]:
    """Close sparse cranked projection paths with bounded inferred end caps.

    Tekla may swap the same physical course between visible and hidden linework
    in neighboring drawings.  Visibility is retained as evidence but is not a
    geometry filter here.  Only low-degree course nodes may receive a bounded
    virtual connector, preventing arbitrary interior topology.
    """

    if target_transverse_mm <= 0:
        raise ValueError("target_transverse_mm must be positive")
    materialized = tuple(
        entity
        for entity in entities
        if entity.layer.casefold() == "part" and entity.kind in {"LINE", "ARC"}
    )
    if not materialized:
        return ()
    graph = build_course_graph(
        materialized,
        frame,
        endpoint_tolerance_mm=endpoint_tolerance_mm,
    )
    curves = _source_curves(materialized, frame, include_hidden=True)
    curves_by_source_id = {curve.source_id: curve for curve in curves}
    node_points = {node.node_id: node.point for node in graph.nodes}
    edges: list[_VirtualCycleEdge] = []
    degree: dict[str, int] = {node.node_id: 0 for node in graph.nodes}
    for course_edge in graph.edges:
        edges.append(
            _VirtualCycleEdge(
                edge_id=f"source:{course_edge.edge_id}",
                start_node=course_edge.start_node,
                end_node=course_edge.end_node,
                source_id=course_edge.source_ids[0],
                virtual=False,
            )
        )
        degree[course_edge.start_node] += 1
        degree[course_edge.end_node] += 1
    maximum_virtual_length = target_transverse_mm * 1.6
    micro_gap_limit = target_transverse_mm * 0.20
    incident_edges: dict[str, list[CourseEdge]] = {
        node.node_id: [] for node in graph.nodes
    }
    for course_edge in graph.edges:
        incident_edges[course_edge.start_node].append(course_edge)
        incident_edges[course_edge.end_node].append(course_edge)

    def has_collinear_course(node: str, toward: Point2) -> bool:
        toward_length = hypot(toward[0], toward[1])
        if toward_length <= 1e-12:
            return False
        direction = (toward[0] / toward_length, toward[1] / toward_length)
        for course_edge in incident_edges[node]:
            other = (
                course_edge.end if course_edge.start_node == node else course_edge.start
            )
            origin = node_points[node]
            dx = other[0] - origin[0]
            dy = other[1] - origin[1]
            length = hypot(dx, dy)
            if length >= target_transverse_mm * 2.0 and abs(
                direction[0] * dx / length + direction[1] * dy / length
            ) >= cos(radians(5.0)):
                return True
        return False

    candidate_nodes = tuple(sorted(node_points))
    for first_index, first in enumerate(candidate_nodes):
        for second in candidate_nodes[first_index + 1 :]:
            first_point = node_points[first]
            second_point = node_points[second]
            distance = hypot(
                second_point[0] - first_point[0],
                second_point[1] - first_point[1],
            )
            if distance <= endpoint_tolerance_mm or distance > maximum_virtual_length:
                continue
            low_degree_cap = degree[first] <= 2 and degree[second] <= 2
            delta = (
                second_point[0] - first_point[0],
                second_point[1] - first_point[1],
            )
            collinear_micro_gap = (
                distance <= micro_gap_limit
                and has_collinear_course(first, delta)
                and has_collinear_course(second, delta)
            )
            if not low_degree_cap and not collinear_micro_gap:
                continue
            edges.append(
                _VirtualCycleEdge(
                    edge_id=f"virtual:{first}:{second}",
                    start_node=first,
                    end_node=second,
                    source_id=None,
                    virtual=True,
                )
            )
    adjacency: dict[str, list[tuple[_VirtualCycleEdge, str]]] = {}
    for cycle_edge in edges:
        adjacency.setdefault(cycle_edge.start_node, []).append(
            (cycle_edge, cycle_edge.end_node)
        )
        adjacency.setdefault(cycle_edge.end_node, []).append(
            (cycle_edge, cycle_edge.start_node)
        )
    for values in adjacency.values():
        values.sort(key=lambda item: item[0].edge_id)

    seen_cycles: set[tuple[str, ...]] = set()
    raw_cycles: list[tuple[tuple[_VirtualCycleEdge, str, str], ...]] = []
    for start in sorted(adjacency):
        stack: list[
            tuple[
                str,
                frozenset[str],
                tuple[tuple[_VirtualCycleEdge, str, str], ...],
                int,
            ]
        ] = [(start, frozenset((start,)), (), 0)]
        while stack and len(raw_cycles) < maximum_cycles:
            node, visited, path, virtual_count = stack.pop()
            for cycle_edge, neighbor in adjacency.get(node, ()):
                if path and cycle_edge.edge_id == path[-1][0].edge_id:
                    continue
                next_virtual_count = virtual_count + int(cycle_edge.virtual)
                if next_virtual_count > maximum_virtual_edges:
                    continue
                if neighbor == start:
                    candidate_path = (*path, (cycle_edge, node, neighbor))
                    source_count = sum(not item[0].virtual for item in candidate_path)
                    if source_count < 4:
                        continue
                    signature = tuple(
                        sorted(item[0].edge_id for item in candidate_path)
                    )
                    if signature in seen_cycles:
                        continue
                    seen_cycles.add(signature)
                    raw_cycles.append(candidate_path)
                    continue
                if neighbor in visited:
                    continue
                stack.append(
                    (
                        neighbor,
                        visited | {neighbor},
                        (*path, (cycle_edge, node, neighbor)),
                        next_virtual_count,
                    )
                )
    accepted_by_bucket: dict[tuple[float, ...], ProjectionFaceCandidate] = {}
    for path in raw_cycles:
        coordinates: list[Point2] = []
        for cycle_edge, from_node, to_node in path:
            segment_coordinates: tuple[Point2, ...]
            if cycle_edge.virtual:
                segment_coordinates = (node_points[from_node], node_points[to_node])
            else:
                assert cycle_edge.source_id is not None
                curve = curves_by_source_id[cycle_edge.source_id]
                segment_coordinates = tuple(
                    (float(point[0]), float(point[1])) for point in curve.line.coords
                )
                desired = node_points[from_node]
                if hypot(
                    segment_coordinates[-1][0] - desired[0],
                    segment_coordinates[-1][1] - desired[1],
                ) < hypot(
                    segment_coordinates[0][0] - desired[0],
                    segment_coordinates[0][1] - desired[1],
                ):
                    segment_coordinates = tuple(reversed(segment_coordinates))
            if coordinates:
                coordinates.extend(segment_coordinates[1:])
            else:
                coordinates.extend(segment_coordinates)
        polygon = Polygon(coordinates)
        if (
            not polygon.is_valid
            or polygon.area < target_transverse_mm * target_transverse_mm
            or polygon.bounds[2] - polygon.bounds[0] < target_transverse_mm * 2.0
        ):
            continue
        candidate = _assess_candidate(
            polygon,
            curves,
            grid_size_mm=0.001,
            endpoint_tolerance_mm=endpoint_tolerance_mm,
        )
        if candidate is None:
            continue
        bucket = _candidate_bucket(
            candidate,
            target_transverse_mm=target_transverse_mm,
        )
        existing = accepted_by_bucket.get(bucket)
        if existing is None or candidate.polygon.area > existing.polygon.area:
            accepted_by_bucket[bucket] = candidate
    accepted = [
        candidate
        for candidate in accepted_by_bucket.values()
        if candidate.polygon.length > 2.0 * target_transverse_mm
        and abs(
            2.0
            * candidate.polygon.area
            / (candidate.polygon.length - 2.0 * target_transverse_mm)
            - target_transverse_mm
        )
        <= target_transverse_mm * effective_width_tolerance_fraction
    ]
    accepted.sort(
        key=lambda candidate: (
            abs(
                2.0
                * candidate.polygon.area
                / (candidate.polygon.length - 2.0 * target_transverse_mm)
                - target_transverse_mm
            ),
            -candidate.polygon.area,
            candidate.polygon.bounds,
            candidate.boundary_source_ids,
        )
    )
    return tuple(accepted[:maximum_candidates])


def search_source_conserving_face_unions(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    target_transverse_mm: float,
    grid_size_mm: float = 0.001,
    transverse_tolerance_mm: float | None = None,
    endpoint_tolerance_mm: float = 0.05,
    maximum_states: int = 50_000,
    run_subset_search: bool = True,
) -> SourceFaceUnionSearchResult:
    """Enumerate connected face unions whose real corners are source-backed.

    Noding a long projection overlay creates apparent polygon corners at the
    overlay's interior.  Such corners are not source endpoints and are rejected,
    preventing a drawing line from becoming a fabricated end cap.  The union of
    every connected polygonized component is assessed before the bounded subset
    search.  This deterministic maximal-material lane is not a replacement for
    non-maximal physical faces; it keeps the strongest direct outline available
    even when overlay partitioning exhausts the subset budget.
    """

    if target_transverse_mm <= 0:
        raise ValueError("target_transverse_mm must be positive")
    if maximum_states < 0:
        raise ValueError("maximum_states must be non-negative")
    materialized_entities = tuple(entities)
    curves = _source_curves(materialized_entities, frame, include_hidden=True)
    faces = polygonize_part_projection(
        materialized_entities,
        frame,
        include_hidden=True,
        grid_size_mm=grid_size_mm,
    )
    if not faces:
        return SourceFaceUnionSearchResult(
            candidates=(),
            subset_search_complete=True,
            state_budget_exhausted=False,
            states_visited=0,
            connected_maximal_candidate_count=0,
            diagnostics=(),
        )
    tolerance = (
        transverse_tolerance_mm
        if transverse_tolerance_mm is not None
        else max(0.20, target_transverse_mm * 0.0015)
    )
    hidden_source_ids = {
        entity.source_id
        for entity in materialized_entities
        if entity.layer.casefold() == "part"
        and is_hidden_projection_linetype(entity.linetype)
    }
    hidden_linework = unary_union(
        [
            set_precision(curve.line, grid_size_mm, mode="valid_output")
            for curve in curves
            if curve.source_id in hidden_source_ids
        ]
    )
    visible_linework = unary_union(
        [
            set_precision(curve.line, grid_size_mm, mode="valid_output")
            for curve in curves
            if curve.source_id not in hidden_source_ids
        ]
    )
    subset_adjacency: list[set[int]] = [set() for _ in faces]
    maximal_adjacency: list[set[int]] = [set() for _ in faces]
    shared_edge_tolerance = max(0.01, grid_size_mm * 2.0)
    for first in range(len(faces)):
        for second in range(first + 1, len(faces)):
            shared = faces[first].boundary.intersection(faces[second].boundary)
            if shared.length <= shared_edge_tolerance:
                continue
            # Complete subset enumeration must retain every source partition:
            # visible multi-view overlays can legitimately split one physical
            # plate into several polygonized faces.  The maximal-material fast
            # lane is stronger: because it may compensate a truncated subset
            # search, it may erase only a boundary proved to be hidden-only.
            subset_adjacency[first].add(second)
            subset_adjacency[second].add(first)
            hidden_overlap = float(shared.intersection(hidden_linework).length)
            visible_overlap = float(shared.intersection(visible_linework).length)
            if (
                hidden_overlap < shared.length - shared_edge_tolerance
                or visible_overlap > shared_edge_tolerance
            ):
                continue
            maximal_adjacency[first].add(second)
            maximal_adjacency[second].add(first)

    accepted_by_bucket: dict[tuple[float, ...], ProjectionFaceCandidate] = {}

    def retain(candidate: ProjectionFaceCandidate) -> None:
        bucket = _candidate_bucket(
            candidate,
            target_transverse_mm=target_transverse_mm,
        )
        existing = accepted_by_bucket.get(bucket)
        if existing is None:
            accepted_by_bucket[bucket] = candidate
            return
        selected = (
            candidate if candidate.polygon.area > existing.polygon.area else existing
        )
        rule_ids = tuple(sorted(set(existing.rule_ids) | set(candidate.rule_ids)))
        accepted_by_bucket[bucket] = replace(selected, rule_ids=rule_ids)

    def connected_components(
        adjacency: list[set[int]],
    ) -> tuple[frozenset[int], ...]:
        remaining = set(range(len(faces)))
        components: list[frozenset[int]] = []
        while remaining:
            seed = min(remaining)
            component: set[int] = set()
            stack = [seed]
            while stack:
                index = stack.pop()
                if index in component:
                    continue
                component.add(index)
                stack.extend(
                    sorted(adjacency[index].difference(component), reverse=True)
                )
            remaining.difference_update(component)
            components.append(frozenset(component))
        return tuple(components)

    maximal_components = connected_components(maximal_adjacency)
    subset_components = connected_components(subset_adjacency)
    subset_component_set = set(subset_components)

    connected_maximal_candidate_count = 0
    for component in maximal_components:
        if component not in subset_component_set:
            continue
        merged = unary_union([faces[index] for index in component])
        if not isinstance(merged, Polygon):
            continue
        transverse = float(merged.bounds[3] - merged.bounds[1])
        if abs(transverse - target_transverse_mm) > tolerance:
            continue
        candidate = _assess_candidate(
            merged,
            curves,
            grid_size_mm=grid_size_mm,
            endpoint_tolerance_mm=endpoint_tolerance_mm,
        )
        if candidate is None:
            continue
        connected_maximal_candidate_count += 1
        retain(
            replace(
                candidate,
                rule_ids=tuple(
                    sorted(
                        {
                            *candidate.rule_ids,
                            CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,
                        }
                    )
                ),
            )
        )

    seen: set[frozenset[int]] = set()
    state_budget_exhausted = False
    if run_subset_search:
        stop = False
        for component in subset_components:
            for seed in sorted(component):
                stack = [frozenset((seed,))]
                while stack:
                    subset = stack.pop()
                    if subset in seen:
                        continue
                    if len(seen) >= maximum_states:
                        state_budget_exhausted = True
                        stop = True
                        break
                    seen.add(subset)
                    merged = unary_union([faces[index] for index in subset])
                    if not isinstance(merged, Polygon):
                        continue
                    transverse = float(merged.bounds[3] - merged.bounds[1])
                    if abs(transverse - target_transverse_mm) <= tolerance:
                        candidate = _assess_candidate(
                            merged,
                            curves,
                            grid_size_mm=grid_size_mm,
                            endpoint_tolerance_mm=endpoint_tolerance_mm,
                        )
                        if candidate is not None:
                            retain(candidate)
                    if transverse > target_transverse_mm + tolerance:
                        continue
                    neighbors = set().union(
                        *(subset_adjacency[index] for index in subset)
                    )
                    for neighbor in sorted(
                        neighbors.intersection(component).difference(subset),
                        reverse=True,
                    ):
                        stack.append(subset | {neighbor})
                if stop:
                    break
            if stop:
                break
    subset_search_complete = run_subset_search and not state_budget_exhausted
    accepted = list(accepted_by_bucket.values())
    accepted.sort(
        key=lambda candidate: (
            -candidate.polygon.area,
            candidate.polygon.bounds,
            candidate.boundary_source_ids,
        )
    )
    diagnostics: list[str] = []
    if connected_maximal_candidate_count:
        diagnostics.append(CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID)
    if not run_subset_search:
        diagnostics.append("BOX.PROJECTION.SOURCE_FACE_SUBSET_SEARCH.PRUNED")
    elif state_budget_exhausted:
        diagnostics.append(
            "BOX.PROJECTION.SOURCE_FACE_SUBSET_SEARCH.STATE_BUDGET_EXHAUSTED"
        )
    return SourceFaceUnionSearchResult(
        candidates=tuple(accepted),
        subset_search_complete=subset_search_complete,
        state_budget_exhausted=state_budget_exhausted,
        states_visited=len(seen),
        connected_maximal_candidate_count=connected_maximal_candidate_count,
        diagnostics=tuple(diagnostics),
    )


def enumerate_source_conserving_face_unions(
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    *,
    target_transverse_mm: float,
    grid_size_mm: float = 0.001,
    transverse_tolerance_mm: float | None = None,
    endpoint_tolerance_mm: float = 0.05,
    maximum_states: int = 50_000,
) -> tuple[ProjectionFaceCandidate, ...]:
    """Compatibility wrapper returning only source-conserving candidates."""

    return search_source_conserving_face_unions(
        entities,
        frame,
        target_transverse_mm=target_transverse_mm,
        grid_size_mm=grid_size_mm,
        transverse_tolerance_mm=transverse_tolerance_mm,
        endpoint_tolerance_mm=endpoint_tolerance_mm,
        maximum_states=maximum_states,
    ).candidates
