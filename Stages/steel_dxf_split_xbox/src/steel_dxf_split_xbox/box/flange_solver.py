from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from heapq import heappop, heappush
from math import hypot

from shapely import normalize, set_precision
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from .course_graph import CourseGraph, build_course_graph
from .manufacturing_ir import (
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
    contour_polygon,
    contour_semantic_key,
    rectangle_contour,
)
from .metadata import BoxMetadata
from .projection_geometry import (
    CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID,
    ProjectionFaceCandidate,
    enumerate_connected_inner_course_cycles,
    enumerate_endpoint_cap_path_cycles,
    polygonize_part_projection,
    search_source_conserving_face_unions,
)
from .projection_lowering import lower_projection_face_to_contour
from .view_solver import ViewAssignmentCandidate
from .web_solver import enumerate_web_outline_candidates


class FlangeDerivation(StrEnum):
    SOURCE_FACE_UNION = "source_face_union"
    ENDPOINT_CAP_PATH_CYCLE = "endpoint_cap_path_cycle"
    CONNECTED_COURSE_CYCLE = "connected_course_cycle"
    COURSE_STATION_RECTANGLE = "course_station_rectangle"
    PAIRED_COURSE_CAP_DEVELOPMENT = "paired_course_cap_development"
    PARALLEL_COURSE_OFFSET_DEVELOPMENT = "parallel_course_offset_development"
    NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES = "neutral_axis_from_paired_web_courses"


PAIRED_CAP_THICKNESS_BOUNDED_SOURCE_BOUNDARY_RULE_ID = (
    "BOX.FLANGE.PAIRED_CAPS.SOURCE_BOUNDARY_WITH_THICKNESS_BOUND"
)


@dataclass(frozen=True, slots=True)
class FlangeOutlineCandidate:
    candidate_id: str
    contour: tuple[ContourSegmentIR, ...]
    projection: ProjectionFaceCandidate
    derivations: tuple[FlangeDerivation, ...]
    source_ids: tuple[str, ...]
    rule_ids: tuple[str, ...]
    support_source_sets: tuple[tuple[str, ...], ...]

    @property
    def longitudinal_span(self) -> float:
        bounds = contour_polygon(self.contour).bounds
        return float(bounds[2] - bounds[0])

    @property
    def transverse_span(self) -> float:
        bounds = contour_polygon(self.contour).bounds
        return float(bounds[3] - bounds[1])

    @property
    def area(self) -> float:
        return float(contour_polygon(self.contour).area)


@dataclass(frozen=True, slots=True)
class FlangeCandidateSearchResult:
    candidates: tuple[FlangeOutlineCandidate, ...]
    direct_face_search_pruned: bool
    direct_face_search_complete: bool
    diagnostics: tuple[str, ...]


def preserves_exact_source_course_authority(
    candidate: FlangeOutlineCandidate,
    course_length_mm: float,
    *,
    tolerance_mm: float = 0.02,
) -> bool:
    """Whether a source face exactly preserves one observed flange course."""

    return (
        candidate.projection.source_conserved
        and FlangeDerivation.SOURCE_FACE_UNION in candidate.derivations
        and abs(candidate.longitudinal_span - course_length_mm) <= tolerance_mm
        and CONNECTED_MAXIMAL_MATERIAL_FACE_RULE_ID in candidate.rule_ids
    )


def _contour_key(contour: tuple[ContourSegmentIR, ...]) -> str:
    return contour_semantic_key(contour)


@dataclass(frozen=True, slots=True)
class _DevelopedCap:
    lower: tuple[float, float]
    upper: tuple[float, float]
    source_ids: tuple[str, ...]
    extended_inner_course: bool


def _bounded_shortest_path_source_ids(
    graph: CourseGraph,
    start_node: str,
    end_node: str,
    maximum_length: float,
) -> tuple[str, ...] | None:
    """Return one local source path without walking around the whole plate."""

    adjacency: dict[str, list[tuple[str, float, tuple[str, ...]]]] = {}
    for edge in graph.edges:
        length = hypot(edge.end[0] - edge.start[0], edge.end[1] - edge.start[1])
        adjacency.setdefault(edge.start_node, []).append(
            (edge.end_node, length, edge.source_ids)
        )
        adjacency.setdefault(edge.end_node, []).append(
            (edge.start_node, length, edge.source_ids)
        )
    queue: list[tuple[float, str, tuple[str, ...]]] = [(0.0, start_node, ())]
    best: dict[str, float] = {start_node: 0.0}
    while queue:
        distance, node_id, source_ids = heappop(queue)
        if distance > best.get(node_id, float("inf")) + 1e-9:
            continue
        if node_id == end_node:
            return tuple(sorted(set(source_ids)))
        for neighbour, edge_length, edge_source_ids in adjacency.get(node_id, ()):
            candidate = distance + edge_length
            if (
                candidate > maximum_length
                or candidate >= best.get(neighbour, float("inf")) - 1e-9
            ):
                continue
            best[neighbour] = candidate
            heappush(
                queue,
                (candidate, neighbour, (*source_ids, *edge_source_ids)),
            )
    return None


def _continuous_source_course_ids(
    graph: CourseGraph,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    tolerance_mm: float = 0.05,
    maximum_endpoint_extension_mm: float = 0.0,
) -> tuple[str, ...] | None:
    """Prove a continuous source course, allowing only bounded end extension.

    Tekla may stop a longitudinal outer course at a thickness-controlled end
    chamfer while the manufacturing edge continues to the proved end cap.  An
    uncovered interior interval is never allowed, and two isolated caps still
    provide no course evidence at all.
    """

    target = LineString((start, end))
    target_length = float(target.length)
    if (
        target_length <= tolerance_mm
        or maximum_endpoint_extension_mm < 0.0
        or target_length <= 2.0 * maximum_endpoint_extension_mm + tolerance_mm
    ):
        return None
    target_axis = (
        (end[0] - start[0]) / target_length,
        (end[1] - start[1]) / target_length,
    )
    supporting_lines: list[LineString] = []
    source_ids: set[str] = set()
    for edge in graph.edges:
        # A curved source edge only shares its endpoints with this candidate
        # course; its endpoint chord is not source geometry and therefore
        # cannot certify a straight manufacturing boundary.
        if edge.kind != "LINE":
            continue
        edge_line = LineString((edge.start, edge.end))
        edge_length = float(edge_line.length)
        if edge_length <= tolerance_mm:
            continue
        edge_axis = (
            (edge.end[0] - edge.start[0]) / edge_length,
            (edge.end[1] - edge.start[1]) / edge_length,
        )
        cross = abs(target_axis[0] * edge_axis[1] - target_axis[1] * edge_axis[0])
        if cross > 0.001745329:
            continue
        overlap = target.intersection(
            edge_line.buffer(tolerance_mm, cap_style="square")
        )
        if overlap.length <= tolerance_mm:
            continue
        supporting_lines.append(edge_line)
        source_ids.update(edge.source_ids)
    if not supporting_lines:
        return None
    coverage = unary_union(supporting_lines).buffer(
        tolerance_mm,
        cap_style="square",
        join_style="mitre",
    )
    required_start = (
        start[0] + target_axis[0] * maximum_endpoint_extension_mm,
        start[1] + target_axis[1] * maximum_endpoint_extension_mm,
    )
    required_end = (
        end[0] - target_axis[0] * maximum_endpoint_extension_mm,
        end[1] - target_axis[1] * maximum_endpoint_extension_mm,
    )
    if not coverage.covers(LineString((required_start, required_end))):
        return None
    return tuple(sorted(source_ids))


def _enumerate_paired_course_cap_faces(
    assignment: ViewAssignmentCandidate,
    target_transverse_mm: float,
    flange_thickness_mm: float,
) -> tuple[ProjectionFaceCandidate, ...]:
    """Close two source-backed end courses with the two flange edge courses.

    Tekla can export a developed flange as four disconnected semantic courses:
    two longitudinal outer edges plus one visible/hidden course path at each
    end.  The end path may be faceted even when the manufacturing boundary is
    one chord.  This pass discovers those paths in the source graph and emits
    the chord-lowered quadrilateral as an explicit hypothesis.
    """

    entities = assignment.b_view.entities
    frame = assignment.b_view.frame
    graph = build_course_graph(entities, frame)
    transverse_tolerance = max(0.20, target_transverse_mm * 0.0002)
    lower_nodes = tuple(
        node
        for node in graph.nodes
        if abs(node.point[1] - frame.transverse_min) <= transverse_tolerance
    )
    upper_nodes = tuple(
        node
        for node in graph.nodes
        if abs(node.point[1] - frame.transverse_max) <= transverse_tolerance
    )
    caps_by_points: dict[tuple[float, float], _DevelopedCap] = {}
    for lower in lower_nodes:
        for upper in upper_nodes:
            if abs(upper.point[0] - lower.point[0]) > target_transverse_mm * 1.25:
                continue
            source_ids = _bounded_shortest_path_source_ids(
                graph,
                lower.node_id,
                upper.node_id,
                target_transverse_mm * 1.75,
            )
            if source_ids is None:
                continue
            key = (round(lower.point[0], 6), round(upper.point[0], 6))
            existing = caps_by_points.get(key)
            cap = _DevelopedCap(
                lower=lower.point,
                upper=upper.point,
                source_ids=source_ids,
                extended_inner_course=False,
            )
            if existing is None or len(cap.source_ids) < len(existing.source_ids):
                caps_by_points[key] = cap

    # A second common Tekla spelling draws the plate's inner end course only:
    # its transverse span is B-2*tf.  Extending that exact source course to the
    # two outer flange planes is a bounded source-line intersection, not a
    # guessed cap.  It is essential at skew ends where projection shortening
    # would otherwise lose one flange-thickness contribution at each side.
    minimum_inner_width = target_transverse_mm - 2.0 * flange_thickness_mm
    for entity in entities:
        if entity.kind != "LINE" or entity.start is None or entity.end is None:
            continue
        start = frame.world_to_local(entity.start)
        end = frame.world_to_local(entity.end)
        transverse_span = abs(end[1] - start[1])
        if not (
            minimum_inner_width - transverse_tolerance
            <= transverse_span
            < target_transverse_mm - transverse_tolerance
        ):
            continue
        lower_x = _line_longitudinal_at_transverse(start, end, frame.transverse_min)
        upper_x = _line_longitudinal_at_transverse(start, end, frame.transverse_max)
        if lower_x is None or upper_x is None:
            continue
        if abs(upper_x - lower_x) > target_transverse_mm * 1.25:
            continue
        cap_key = (round(lower_x, 6), round(upper_x, 6))
        caps_by_points.setdefault(
            cap_key,
            _DevelopedCap(
                lower=(lower_x, frame.transverse_min),
                upper=(upper_x, frame.transverse_max),
                source_ids=(entity.source_id,),
                extended_inner_course=True,
            ),
        )

    caps = tuple(
        sorted(
            caps_by_points.values(),
            key=lambda cap: (cap.lower[0] + cap.upper[0], cap.lower, cap.upper),
        )
    )
    faces: dict[str, ProjectionFaceCandidate] = {}
    for left_index, left in enumerate(caps):
        for right in caps[left_index + 1 :]:
            if (
                right.lower[0] <= left.lower[0]
                or right.upper[0] <= left.upper[0]
                or (right.lower[0] - left.lower[0] + right.upper[0] - left.upper[0])
                / 2.0
                < target_transverse_mm * 0.25
            ):
                continue
            polygon = Polygon((left.lower, right.lower, right.upper, left.upper))
            if not polygon.is_valid or polygon.area <= target_transverse_mm**2 * 0.10:
                continue
            lower_course_ids = _continuous_source_course_ids(
                graph,
                left.lower,
                right.lower,
                maximum_endpoint_extension_mm=flange_thickness_mm,
            )
            upper_course_ids = _continuous_source_course_ids(
                graph,
                left.upper,
                right.upper,
                maximum_endpoint_extension_mm=flange_thickness_mm,
            )
            if lower_course_ids is None or upper_course_ids is None:
                continue
            face_key = normalize(set_precision(polygon, 0.001)).wkb_hex
            source_ids = tuple(
                sorted(
                    set(left.source_ids)
                    | set(right.source_ids)
                    | set(lower_course_ids)
                    | set(upper_course_ids)
                )
            )
            extension_count = sum(
                (left.extended_inner_course, right.extended_inner_course)
            )
            faces[face_key] = ProjectionFaceCandidate(
                polygon=polygon,
                boundary_source_ids=source_ids,
                vertex_source_ids=source_ids,
                source_conserved=True,
                grid_size_mm=0.001,
                rule_ids=(
                    f"BOX.FLANGE.PAIRED_CAPS.EXTENDED_INNER_COUNT_{extension_count}",
                    PAIRED_CAP_THICKNESS_BOUNDED_SOURCE_BOUNDARY_RULE_ID,
                ),
            )
    return tuple(faces[key] for key in sorted(faces))


def _line_longitudinal_at_transverse(
    start: tuple[float, float],
    end: tuple[float, float],
    transverse: float,
) -> float | None:
    delta = end[1] - start[1]
    if abs(delta) <= 1e-9:
        return None
    ratio = (transverse - start[1]) / delta
    return start[0] + ratio * (end[0] - start[0])


def _replace_polygon_edge(
    coordinates: tuple[tuple[float, float], ...],
    edge_index: int,
    replacement_start: tuple[float, float],
    replacement_end: tuple[float, float],
) -> Polygon:
    values = list(coordinates)
    values[edge_index] = replacement_start
    values[(edge_index + 1) % len(values)] = replacement_end
    return Polygon(values)


def _shift_opposite_polygon_vertices(
    coordinates: tuple[tuple[float, float], ...],
    edge_index: int,
    longitudinal_shift: float,
) -> Polygon:
    edge_vertices = {edge_index, (edge_index + 1) % len(coordinates)}
    values = tuple(
        point if index in edge_vertices else (point[0] + longitudinal_shift, point[1])
        for index, point in enumerate(coordinates)
    )
    return Polygon(values)


def _enumerate_parallel_course_offset_faces(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
    direct_faces: tuple[ProjectionFaceCandidate, ...],
) -> tuple[ProjectionFaceCandidate, ...]:
    """Transfer Tekla's parallel inner-course offset into developed length.

    At a skew end, the projected outer and inner plate edges are parallel but
    displaced longitudinally.  A flat plate keeps the source outer end shape;
    the displacement is therefore transferred to its opposite end (or the
    inner station itself becomes the end).  Enumerating both named lowering
    alternatives keeps role selection outside this local geometry pass.
    """

    frame = assignment.b_view.frame
    target = metadata.profile.value.width
    flange_thickness = metadata.profile.value.flange_thickness
    minimum_inner_width = target - 2.0 * max(
        metadata.profile.value.web_thickness,
        flange_thickness,
    )
    tolerance = max(0.20, target * 0.0002)
    local_lines: list[tuple[str, tuple[float, float], tuple[float, float], float]] = []
    for entity in assignment.b_view.entities:
        if entity.kind != "LINE" or entity.start is None or entity.end is None:
            continue
        start = frame.world_to_local(entity.start)
        end = frame.world_to_local(entity.end)
        transverse_span = abs(end[1] - start[1])
        if not (
            minimum_inner_width - tolerance <= transverse_span < target - tolerance
        ):
            continue
        local_lines.append((entity.source_id, start, end, transverse_span))

    result: dict[str, ProjectionFaceCandidate] = {}
    for face in direct_faces:
        coordinates = tuple(
            (float(x), float(y)) for x, y in tuple(face.polygon.exterior.coords)[:-1]
        )
        for edge_index, edge_start in enumerate(coordinates):
            edge_end = coordinates[(edge_index + 1) % len(coordinates)]
            edge_transverse_span = abs(edge_end[1] - edge_start[1])
            if abs(edge_transverse_span - target) > tolerance:
                continue
            edge_dx = edge_end[0] - edge_start[0]
            edge_dy = edge_end[1] - edge_start[1]
            edge_length = hypot(edge_dx, edge_dy)
            if edge_length <= target * 0.90:
                continue
            for source_id, inner_start, inner_end, inner_length in local_lines:
                inner_dx = inner_end[0] - inner_start[0]
                inner_dy = inner_end[1] - inner_start[1]
                parallel_residual = abs(edge_dx * inner_dy - edge_dy * inner_dx) / (
                    edge_length * max(inner_length, 1e-9)
                )
                if parallel_residual > 0.001:
                    continue
                replacement_start_x = _line_longitudinal_at_transverse(
                    inner_start, inner_end, edge_start[1]
                )
                replacement_end_x = _line_longitudinal_at_transverse(
                    inner_start, inner_end, edge_end[1]
                )
                if replacement_start_x is None or replacement_end_x is None:
                    continue
                first_offset = replacement_start_x - edge_start[0]
                second_offset = replacement_end_x - edge_end[0]
                if (
                    abs(first_offset - second_offset) > tolerance
                    or abs(first_offset) < tolerance
                    or abs(first_offset) > flange_thickness * 2.5
                ):
                    continue
                replacement = _replace_polygon_edge(
                    coordinates,
                    edge_index,
                    (replacement_start_x, edge_start[1]),
                    (replacement_end_x, edge_end[1]),
                )
                opposite = _shift_opposite_polygon_vertices(
                    coordinates,
                    edge_index,
                    -first_offset,
                )
                source_ids = tuple(
                    sorted(
                        set(face.boundary_source_ids)
                        | set(face.vertex_source_ids)
                        | {source_id}
                    )
                )
                for polygon in (replacement, opposite):
                    if (
                        not polygon.is_valid
                        or polygon.area <= target**2
                        or abs((polygon.bounds[3] - polygon.bounds[1]) - target)
                        > tolerance
                    ):
                        continue
                    key = normalize(set_precision(polygon, 0.001)).wkb_hex
                    result[key] = ProjectionFaceCandidate(
                        polygon=polygon,
                        boundary_source_ids=source_ids,
                        vertex_source_ids=source_ids,
                        source_conserved=True,
                        grid_size_mm=0.001,
                        rule_ids=("BOX.FLANGE.PARALLEL_COURSE_OFFSET",),
                    )
    return tuple(result[key] for key in sorted(result))


@dataclass(frozen=True, slots=True)
class _LogicalBoundaryEdge:
    start: tuple[float, float]
    end: tuple[float, float]
    length: float
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _BoundaryCapSpan:
    edge_indices: frozenset[int]
    path_length: float
    transverse_displacement: float


def _logical_boundary_edges(
    contour: tuple[ContourSegmentIR, ...],
) -> tuple[_LogicalBoundaryEdge, ...]:
    """Collapse Tekla micro-facets that retain one source-course identity."""

    groups: list[list[ContourSegmentIR]] = []
    for segment in contour:
        if groups and groups[-1][-1].evidence.source_ids == segment.evidence.source_ids:
            groups[-1].append(segment)
        else:
            groups.append([segment])
    if (
        len(groups) > 1
        and groups[0][0].evidence.source_ids == groups[-1][0].evidence.source_ids
    ):
        groups[0] = [*groups[-1], *groups[0]]
        groups.pop()
    return tuple(
        _LogicalBoundaryEdge(
            start=group[0].start,
            end=group[-1].end,
            length=sum(
                hypot(
                    segment.end[0] - segment.start[0],
                    segment.end[1] - segment.start[1],
                )
                for segment in group
            ),
            source_ids=tuple(
                sorted(
                    {
                        source_id
                        for segment in group
                        for source_id in segment.evidence.source_ids
                    }
                )
            ),
        )
        for group in groups
    )


def _enumerate_neutral_axis_flange_lengths(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
) -> tuple[tuple[float, tuple[str, ...]], ...]:
    """Develop flange lengths from paired cranked web boundary courses.

    A polybeam end can make the two web courses different lengths.  Tekla's
    flat-plate convention locates the two flange neutral courses at the two
    quarter points between those boundary lengths.  Both exact and drawing-mm
    rounded hypotheses are retained; later assembly proofs choose the one
    supported by the complete member.
    """

    profile = metadata.profile.value
    if assignment.h_view.frame.transverse_span <= profile.height * 1.5:
        return ()
    web_candidates = enumerate_web_outline_candidates(assignment, metadata).candidates
    result: dict[float, set[tuple[str, ...]]] = {}
    target = profile.web_clear_width
    for web in web_candidates:
        edges = _logical_boundary_edges(web.contour)
        caps_by_edges: dict[frozenset[int], _BoundaryCapSpan] = {}
        for start in range(len(edges)):
            for count in (1, 2):
                indices = tuple(
                    (start + offset) % len(edges) for offset in range(count)
                )
                edge_indices = frozenset(indices)
                cap = _BoundaryCapSpan(
                    edge_indices=edge_indices,
                    path_length=sum(edges[index].length for index in indices),
                    transverse_displacement=abs(
                        edges[indices[-1]].end[1] - edges[indices[0]].start[1]
                    ),
                )
                if (
                    target * 0.75 <= cap.path_length <= target * 1.60
                    and cap.transverse_displacement >= target * 0.70
                ):
                    caps_by_edges[edge_indices] = cap
        caps = tuple(caps_by_edges.values())
        course_pairs: list[tuple[float, float, float]] = []
        for first_position, first_cap in enumerate(caps):
            for second_cap in caps[first_position + 1 :]:
                if first_cap.edge_indices & second_cap.edge_indices:
                    continue
                remaining = (
                    set(range(len(edges)))
                    - set(first_cap.edge_indices)
                    - set(second_cap.edge_indices)
                )
                components: list[set[int]] = []
                while remaining:
                    seed = remaining.pop()
                    component = {seed}
                    stack = [seed]
                    while stack:
                        index = stack.pop()
                        for neighbour in (
                            (index - 1) % len(edges),
                            (index + 1) % len(edges),
                        ):
                            if neighbour in remaining:
                                remaining.remove(neighbour)
                                component.add(neighbour)
                                stack.append(neighbour)
                    components.append(component)
                if len(components) != 2:
                    continue
                first_course, second_course = (
                    sum(edges[index].length for index in component)
                    for component in components
                )
                shorter, longer = sorted((first_course, second_course))
                if (
                    shorter < metadata.nominal_length.value * 0.50
                    or longer > metadata.nominal_length.value * 1.25
                    or longer - shorter > target * 0.25
                ):
                    continue
                cap_residual = abs(first_cap.path_length - profile.height) + abs(
                    second_cap.path_length - profile.height
                )
                course_pairs.append((cap_residual, shorter, longer))
        if not course_pairs:
            continue
        best_cap_residual = min(item[0] for item in course_pairs)
        for cap_residual, shorter, longer in course_pairs:
            if cap_residual > best_cap_residual + 0.001:
                continue
            source_ids = tuple(sorted(web.source_ids))
            quarter_lengths = (
                (3.0 * shorter + longer) / 4.0,
                (shorter + 3.0 * longer) / 4.0,
            )
            for length in quarter_lengths:
                for hypothesis in (length, float(round(length))):
                    result.setdefault(hypothesis, set()).add(source_ids)
    return tuple(
        (length, source_ids)
        for length in sorted(result)
        for source_ids in sorted(result[length])
    )


def enumerate_flange_outline_candidates(
    assignment: ViewAssignmentCandidate,
    metadata: BoxMetadata,
    *,
    maximum_direct_faces: int = 45,
) -> FlangeCandidateSearchResult:
    """Enumerate full-width BOX flange plates from the B-direction projection."""

    entities = assignment.b_view.entities
    frame = assignment.b_view.frame
    profile = metadata.profile.value
    target = profile.width
    endpoint_cycles = enumerate_endpoint_cap_path_cycles(
        entities,
        frame,
        target_transverse_mm=target,
    )
    connected_cycles = enumerate_connected_inner_course_cycles(
        entities,
        frame,
        target_transverse_mm=target,
    )
    paired_cap_faces = _enumerate_paired_course_cap_faces(
        assignment,
        target,
        profile.flange_thickness,
    )
    faces = polygonize_part_projection(
        entities,
        frame,
        include_hidden=True,
    )
    direct_face_search_pruned = len(faces) >= maximum_direct_faces and bool(
        endpoint_cycles or connected_cycles
    )
    direct_search = search_source_conserving_face_unions(
        entities,
        frame,
        target_transverse_mm=target,
        run_subset_search=not direct_face_search_pruned,
    )
    direct = direct_search.candidates
    parallel_offset_faces = _enumerate_parallel_course_offset_faces(
        assignment,
        metadata,
        direct,
    )
    channels = (
        (FlangeDerivation.SOURCE_FACE_UNION, direct),
        (FlangeDerivation.ENDPOINT_CAP_PATH_CYCLE, endpoint_cycles),
        (FlangeDerivation.CONNECTED_COURSE_CYCLE, connected_cycles),
        (
            FlangeDerivation.PAIRED_COURSE_CAP_DEVELOPMENT,
            paired_cap_faces,
        ),
        (
            FlangeDerivation.PARALLEL_COURSE_OFFSET_DEVELOPMENT,
            parallel_offset_faces,
        ),
    )
    by_key: dict[str, FlangeOutlineCandidate] = {}
    for derivation, projections in channels:
        for projection in projections:
            contour = lower_projection_face_to_contour(
                projection,
                entities,
                frame,
                profile,
            )
            polygon = contour_polygon(contour)
            transverse_span = float(polygon.bounds[3] - polygon.bounds[1])
            longitudinal_span = float(polygon.bounds[2] - polygon.bounds[0])
            if abs(transverse_span - target) > max(
                0.20, target * 0.0002
            ) or longitudinal_span <= max(
                profile.flange_thickness * 2.0, target * 0.25
            ):
                continue
            contour_key = _contour_key(contour)
            source_ids = tuple(
                sorted(
                    set(projection.boundary_source_ids)
                    | set(projection.vertex_source_ids)
                )
            )
            existing = by_key.get(contour_key)
            if existing is None:
                by_key[contour_key] = FlangeOutlineCandidate(
                    candidate_id=(
                        "flange:" + sha256(contour_key.encode("ascii")).hexdigest()[:16]
                    ),
                    contour=contour,
                    projection=projection,
                    derivations=(derivation,),
                    source_ids=source_ids,
                    rule_ids=projection.rule_ids,
                    support_source_sets=(source_ids,),
                )
                continue
            by_key[contour_key] = replace(
                existing,
                derivations=tuple(sorted({*existing.derivations, derivation}, key=str)),
                source_ids=tuple(sorted(set(existing.source_ids) | set(source_ids))),
                rule_ids=tuple(
                    sorted(set(existing.rule_ids) | set(projection.rule_ids))
                ),
                support_source_sets=tuple(
                    sorted(set(existing.support_source_sets) | {source_ids})
                ),
            )

    # Straight developed flange edges are often exported as disjoint course
    # stations rather than one closed face.  Every station remains source- or
    # source-intersection-backed; assembly search decides which two intervals
    # are the physical top/bottom plates.
    stations: dict[float, set[str]] = {}
    for entity in entities:
        for station_point in (entity.start, entity.end):
            if station_point is None:
                continue
            longitudinal = frame.world_to_local(station_point)[0]
            station = round(longitudinal / 0.001) * 0.001
            stations.setdefault(station, set()).add(entity.source_id)
    for face in faces:
        for vertex in tuple(face.exterior.coords)[:-1]:
            station = round(float(vertex[0]) / 0.001) * 0.001
            stations.setdefault(station, set()).update(
                source_id
                for projection in (*direct, *endpoint_cycles, *connected_cycles)
                for source_id in projection.vertex_source_ids
            )
    ordered_stations = tuple(sorted(stations))
    evidence = FeatureEvidence(
        state=EvidenceState.INFERRED,
        source_ids=tuple(
            sorted({item for values in stations.values() for item in values})
        ),
        rule_ids=("BOX.FLANGE.COURSE_STATION_DEVELOPMENT",),
        proof_ids=("BOX.PROOF.FLANGE.FULL_WIDTH",),
        description="full-width flange developed between source-backed course stations",
    )
    for first_index, first in enumerate(ordered_stations):
        for second in ordered_stations[first_index + 1 :]:
            length = second - first
            if length < target or length > frame.longitudinal_span * 1.05:
                continue
            contour = rectangle_contour(0.0, 0.0, length, target, evidence)
            key = _contour_key(contour)
            if key in by_key:
                existing = by_key[key]
                by_key[key] = replace(
                    existing,
                    derivations=tuple(
                        sorted(
                            {
                                *existing.derivations,
                                FlangeDerivation.COURSE_STATION_RECTANGLE,
                            },
                            key=str,
                        )
                    ),
                    rule_ids=tuple(
                        sorted(
                            set(existing.rule_ids)
                            | {"BOX.FLANGE.COURSE_STATION_DEVELOPMENT"}
                        )
                    ),
                    support_source_sets=tuple(
                        sorted(
                            set(existing.support_source_sets)
                            | {tuple(sorted(stations[first] | stations[second]))}
                        )
                    ),
                )
                continue
            polygon = contour_polygon(contour)
            projection = ProjectionFaceCandidate(
                polygon=polygon,
                boundary_source_ids=evidence.source_ids,
                vertex_source_ids=tuple(sorted(stations[first] | stations[second])),
                source_conserved=True,
                grid_size_mm=0.001,
            )
            by_key[key] = FlangeOutlineCandidate(
                candidate_id=f"flange:{sha256(key.encode('ascii')).hexdigest()[:16]}",
                contour=contour,
                projection=projection,
                derivations=(FlangeDerivation.COURSE_STATION_RECTANGLE,),
                source_ids=projection.vertex_source_ids,
                rule_ids=("BOX.FLANGE.COURSE_STATION_DEVELOPMENT",),
                support_source_sets=(projection.vertex_source_ids,),
            )

    neutral_evidence_base = FeatureEvidence(
        state=EvidenceState.INFERRED,
        source_ids=(),
        rule_ids=("BOX.FLANGE.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES",),
        proof_ids=("BOX.PROOF.FLANGE.CRANKED_WEB_COURSE_PAIR",),
        description="flange neutral length from quarter points of paired web courses",
    )
    for length, source_ids in _enumerate_neutral_axis_flange_lengths(
        assignment,
        metadata,
    ):
        evidence = replace(neutral_evidence_base, source_ids=source_ids)
        contour = rectangle_contour(0.0, 0.0, length, target, evidence)
        key = _contour_key(contour)
        if key in by_key:
            existing = by_key[key]
            by_key[key] = replace(
                existing,
                derivations=tuple(
                    sorted(
                        {
                            *existing.derivations,
                            FlangeDerivation.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES,
                        },
                        key=str,
                    )
                ),
                source_ids=tuple(sorted(set(existing.source_ids) | set(source_ids))),
                rule_ids=tuple(
                    sorted(
                        set(existing.rule_ids)
                        | {"BOX.FLANGE.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES"}
                    )
                ),
                support_source_sets=tuple(
                    sorted(set(existing.support_source_sets) | {source_ids})
                ),
            )
            continue
        polygon = contour_polygon(contour)
        projection = ProjectionFaceCandidate(
            polygon=polygon,
            boundary_source_ids=source_ids,
            vertex_source_ids=source_ids,
            source_conserved=True,
            grid_size_mm=0.001,
        )
        by_key[key] = FlangeOutlineCandidate(
            candidate_id=f"flange:{sha256(key.encode('ascii')).hexdigest()[:16]}",
            contour=contour,
            projection=projection,
            derivations=(FlangeDerivation.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES,),
            source_ids=source_ids,
            rule_ids=("BOX.FLANGE.NEUTRAL_AXIS_FROM_PAIRED_WEB_COURSES",),
            support_source_sets=(source_ids,),
        )
    candidates = list(by_key.values())
    candidates.sort(
        key=lambda candidate: (
            -candidate.area,
            -candidate.longitudinal_span,
            candidate.candidate_id,
        )
    )
    diagnostics = tuple(
        dict.fromkeys(
            (
                *(
                    ("BOX.FLANGE.DIRECT_FACE_SUBSET_SEARCH.PRUNED",)
                    if direct_face_search_pruned
                    else ()
                ),
                *direct_search.diagnostics,
            )
        )
    )
    return FlangeCandidateSearchResult(
        candidates=tuple(candidates),
        direct_face_search_pruned=direct_face_search_pruned,
        direct_face_search_complete=direct_search.subset_search_complete,
        diagnostics=diagnostics,
    )
