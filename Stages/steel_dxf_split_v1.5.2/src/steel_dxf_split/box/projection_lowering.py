from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import atan2, hypot, pi, radians, tan

from shapely.geometry import LineString, Point

from .manufacturing_ir import (
    ContourSegmentIR,
    EvidenceState,
    FeatureEvidence,
)
from .metadata import BoxProfile
from .projection_geometry import (
    ProjectionFaceCandidate,
    _angle_in_sweep,
    _source_curves,
    _SourceCurve,
    _vertex_authority,
)
from .source_ir import SourceEntityIR
from .view_frame import Point2, ViewFrame


@dataclass(frozen=True, slots=True)
class _AtomicBoundary:
    start: Point2
    end: Point2
    arc: _SourceCurve | None
    source_ids: tuple[str, ...]


def _point_on_arc(point: Point2, arc: _SourceCurve, tolerance: float) -> bool:
    assert arc.arc_center is not None
    assert arc.arc_radius is not None
    assert arc.arc_start_angle is not None
    assert arc.arc_sweep is not None
    dx = point[0] - arc.arc_center[0]
    dy = point[1] - arc.arc_center[1]
    if abs(hypot(dx, dy) - arc.arc_radius) > tolerance:
        return False
    from math import degrees

    angle = degrees(atan2(dy, dx)) % 360.0
    angular_tolerance = max(0.01, tolerance / arc.arc_radius * 57.3)
    return _angle_in_sweep(
        angle,
        arc.arc_start_angle,
        arc.arc_sweep,
        angular_tolerance,
    )


def _matching_arc(
    start: Point2,
    end: Point2,
    arcs: tuple[_SourceCurve, ...],
    tolerance: float,
    maximum_projection_fillet_radius: float,
) -> _SourceCurve | None:
    midpoint = Point(((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0))
    matches = tuple(
        arc
        for arc in arcs
        if _point_on_arc(start, arc, tolerance)
        and _point_on_arc(end, arc, tolerance)
        and arc.line.distance(midpoint) <= tolerance
    )
    direct = min(
        matches,
        key=lambda arc: (arc.arc_radius or 0.0, arc.source_id),
        default=None,
    )
    if direct is not None:
        return direct

    # Tekla sometimes facets the first/last few degrees of a displayed fillet
    # as short LINEs.  Associate those tangent fragments with the adjacent
    # exact small ARC so the manufacturing lowering handles one whole fillet.
    chord_length = hypot(end[0] - start[0], end[1] - start[1])
    extensions: list[_SourceCurve] = []
    for arc in arcs:
        assert arc.arc_center is not None
        assert arc.arc_radius is not None
        assert arc.arc_start_angle is not None
        assert arc.arc_sweep is not None
        if (
            arc.arc_radius > maximum_projection_fillet_radius
            or chord_length > arc.arc_radius * 0.35
        ):
            continue
        on_circle = True
        for point in (start, end):
            dx = point[0] - arc.arc_center[0]
            dy = point[1] - arc.arc_center[1]
            if abs(hypot(dx, dy) - arc.arc_radius) > tolerance:
                on_circle = False
                break
        if not on_circle:
            continue
        # A same-circle chord is a Tekla ARC continuation only when it touches
        # one exact source-ARC endpoint.  This prevents a nearby real chamfer
        # or short cut edge from being reclassified merely because it is short.
        if (
            min(
                hypot(point[0] - endpoint[0], point[1] - endpoint[1])
                for point in (start, end)
                for endpoint in arc.endpoints
            )
            <= tolerance
        ):
            extensions.append(arc)
    return min(
        extensions,
        key=lambda arc: (arc.arc_radius or 0.0, arc.source_id),
        default=None,
    )


def _line_sources(
    start: Point2,
    end: Point2,
    curves: tuple[_SourceCurve, ...],
    tolerance: float,
) -> tuple[str, ...]:
    segment = LineString((start, end))
    direct = {
        curve.source_id
        for curve in curves
        if curve.arc_center is None
        and curve.line.buffer(tolerance, cap_style="flat").covers(segment)
    }
    if direct:
        return tuple(sorted(direct))
    return tuple(
        sorted(
            set(_vertex_authority(start, curves, tolerance=tolerance))
            | set(_vertex_authority(end, curves, tolerance=tolerance))
        )
    )


def _arc_bulge(arc: _SourceCurve, start: Point2, end: Point2) -> float:
    assert arc.arc_center is not None
    assert arc.arc_sweep is not None
    first = atan2(start[1] - arc.arc_center[1], start[0] - arc.arc_center[0])
    second = atan2(end[1] - arc.arc_center[1], end[0] - arc.arc_center[0])
    ccw = (second - first) % (2.0 * 3.141592653589793)
    clockwise = (first - second) % (2.0 * 3.141592653589793)
    source_sweep = radians(arc.arc_sweep)
    if ccw <= source_sweep + radians(0.1):
        return tan(ccw / 4.0)
    if clockwise <= source_sweep + radians(0.1):
        return -tan(clockwise / 4.0)
    raise ValueError("projection boundary traverses outside its source ARC")


def _arc_group_bulge(group: list[_AtomicBoundary]) -> float:
    """Recover one circular course from exact ARC and transition atoms."""

    arc = next(atom.arc for atom in group if atom.arc is not None)
    assert arc.arc_center is not None
    points = (group[0].start, *(atom.end for atom in group))
    angles = tuple(
        atan2(point[1] - arc.arc_center[1], point[0] - arc.arc_center[0])
        for point in points
    )
    sweep = 0.0
    for first, second in zip(angles, angles[1:], strict=False):
        sweep += (second - first + pi) % (2.0 * pi) - pi
    if abs(sweep) <= 1e-12 or abs(sweep) >= pi:
        raise ValueError("projection fillet has no unique minor circular sweep")
    return tan(sweep / 4.0)


def lower_projection_face_to_contour(
    candidate: ProjectionFaceCandidate,
    entities: Iterable[SourceEntityIR],
    frame: ViewFrame,
    profile: BoxProfile,
    *,
    matching_tolerance_mm: float = 0.15,
) -> tuple[ContourSegmentIR, ...]:
    """Lower one source projection cycle into an explicit plate contour.

    Exact Tekla projection arcs remain manufacturing bulges.  Short LINE
    transition chords may join the same circular course only when their source
    geometry proves the shared circle and ARC endpoint.  The distinction is
    based on section plate thickness, never on a drawing/member identifier.
    """

    curves = _source_curves(tuple(entities), frame, include_hidden=True)
    arcs = tuple(curve for curve in curves if curve.arc_center is not None)
    maximum_projection_fillet_radius = 2.0 * max(
        profile.web_thickness,
        profile.flange_thickness,
    )
    points = tuple(
        (float(point[0]), float(point[1]))
        for point in tuple(candidate.polygon.exterior.coords)[:-1]
    )
    atoms = [
        _AtomicBoundary(
            start=start,
            end=points[(index + 1) % len(points)],
            arc=_matching_arc(
                start,
                points[(index + 1) % len(points)],
                arcs,
                matching_tolerance_mm,
                maximum_projection_fillet_radius,
            ),
            source_ids=(),
        )
        for index, start in enumerate(points)
    ]
    atoms = [
        _AtomicBoundary(
            start=atom.start,
            end=atom.end,
            arc=atom.arc,
            source_ids=(atom.arc.source_id,)
            if atom.arc is not None
            else _line_sources(atom.start, atom.end, curves, matching_tolerance_mm),
        )
        for atom in atoms
    ]
    # Rotate the cyclic list onto a semantic boundary so one source ARC is not
    # split into two output groups merely because Polygon chose that start.
    if atoms and atoms[0].arc is not None and atoms[-1].arc is not None:
        for index in range(len(atoms)):
            previous = atoms[index - 1].arc
            current = atoms[index].arc
            if (previous.source_id if previous else None) != (
                current.source_id if current else None
            ):
                atoms = atoms[index:] + atoms[:index]
                break

    origin_x, origin_y = candidate.polygon.bounds[:2]
    small_fillet = [
        atom.arc is not None
        and (atom.arc.arc_radius or 0.0) <= maximum_projection_fillet_radius
        for atom in atoms
    ]

    def group_key(index: int) -> tuple[object, ...]:
        atom = atoms[index]
        if small_fillet[index]:
            assert atom.arc is not None
            assert atom.arc.arc_center is not None
            assert atom.arc.arc_radius is not None
            return (
                "fillet",
                round(atom.arc.arc_center[0] / matching_tolerance_mm),
                round(atom.arc.arc_center[1] / matching_tolerance_mm),
                round(atom.arc.arc_radius / matching_tolerance_mm),
            )
        if atom.arc is not None:
            return ("arc", atom.arc.source_id)
        return ("line", str(index))

    if atoms:
        for index in range(len(atoms)):
            if group_key(index - 1) != group_key(index):
                atoms = atoms[index:] + atoms[:index]
                small_fillet = small_fillet[index:] + small_fillet[:index]
                break
    groups: list[tuple[list[_AtomicBoundary], bool]] = []
    for index, atom in enumerate(atoms):
        if groups and (
            (
                small_fillet[index]
                and groups[-1][1]
                and group_key(index) == group_key(index - 1)
            )
            or (
                not small_fillet[index]
                and not groups[-1][1]
                and atom.arc is not None
                and groups[-1][0][-1].arc is not None
                and atom.arc.source_id == groups[-1][0][-1].arc.source_id
            )
        ):
            groups[-1][0].append(atom)
        else:
            groups.append(([atom], small_fillet[index]))

    segments: list[ContourSegmentIR] = []
    for index, (group, is_projection_fillet) in enumerate(groups):
        start = group[0].start
        end = group[-1].end
        arc = group[0].arc
        source_ids = tuple(sorted({item for atom in group for item in atom.source_ids}))
        if is_projection_fillet:
            bulge = _arc_group_bulge(group)
            state = EvidenceState.INFERRED
            rule_ids = ("BOX.LOWER.PROJECTION_FILLET_TO_SOURCE_ARC",)
            description = (
                "exact Tekla ARC and proven transition chords retained as a "
                "manufacturing bulge"
            )
        elif arc is not None:
            bulge = _arc_bulge(arc, start, end)
            state = EvidenceState.DIRECT
            rule_ids = ("BOX.LOWER.SOURCE_ARC",)
            description = "source course ARC retained as a manufacturing arc"
        else:
            bulge = 0.0
            state = EvidenceState.DIRECT if source_ids else EvidenceState.INFERRED
            rule_id = (
                "BOX.LOWER.SOURCE_LINE"
                if source_ids
                else "BOX.LOWER.INNER_COURSE_EXTENSION"
            )
            rule_ids = (rule_id,)
            description = "source line or guarded inner-course extension"
        evidence = FeatureEvidence(
            state=state,
            source_ids=source_ids or candidate.boundary_source_ids,
            rule_ids=rule_ids,
            proof_ids=("BOX.PROOF.PROJECTION_TO_MANUFACTURING",),
            description=description,
        )
        segments.append(
            ContourSegmentIR(
                segment_id=f"projection:{index:04d}",
                start=(start[0] - origin_x, start[1] - origin_y),
                end=(end[0] - origin_x, end[1] - origin_y),
                bulge=bulge,
                evidence=evidence,
            )
        )
    return tuple(segments)
