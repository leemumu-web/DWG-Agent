from __future__ import annotations

from math import atan2, cos, degrees, hypot, isfinite, radians, sin
from typing import Sequence

from .model import Point


MAX_SUPPORTED_ABS_BULGE = 1_000_000.0


def _require_finite(value: float, name: str) -> None:
    if not isfinite(value):
        raise ValueError(f"non-finite {name}")


def _swept_arc_points(
    center_x: float,
    center_y: float,
    radius: float,
    start_degrees: float,
    sweep_degrees: float,
    max_step_degrees: float = 5.0,
    additional_extrema_degrees: Sequence[float] = (),
) -> list[Point]:
    """Sample an oriented circular course while retaining every X/Y extremum."""
    if radius <= 0.0 or abs(sweep_degrees) <= 1e-12:
        angle = radians(start_degrees)
        return [(center_x + radius * cos(angle), center_y + radius * sin(angle))]

    end_degrees = start_degrees + sweep_degrees
    count = max(4, int(abs(sweep_degrees) / max_step_degrees) + 1)
    angles = [
        start_degrees + sweep_degrees * index / count
        for index in range(count + 1)
    ]
    # Local X/Y extrema retain a curve's envelope without a transform.  A
    # caller may add source angles derived from a later affine placement: the
    # global X/Y extrema of a transformed circle are generally not local
    # cardinal angles (for example, an ARC in a rotated INSERT).
    for extremum in (0.0, 90.0, 180.0, 270.0, *additional_extrema_degrees):
        candidate = extremum
        if sweep_degrees > 0.0:
            while candidate < start_degrees - 1e-12:
                candidate += 360.0
            if candidate <= end_degrees + 1e-12:
                angles.append(candidate)
        else:
            while candidate > start_degrees + 1e-12:
                candidate -= 360.0
            if candidate >= end_degrees - 1e-12:
                angles.append(candidate)
    angles.sort(reverse=sweep_degrees < 0.0)
    unique_angles: list[float] = []
    for angle in angles:
        if not unique_angles or abs(angle - unique_angles[-1]) > 1e-12:
            unique_angles.append(angle)
    return [
        (center_x + radius * cos(radians(angle)), center_y + radius * sin(radians(angle)))
        for angle in unique_angles
    ]


def arc_points(
    center_x: float,
    center_y: float,
    radius: float,
    start_degrees: float,
    end_degrees: float,
    max_step_degrees: float = 5.0,
    additional_extrema_degrees: Sequence[float] = (),
) -> list[Point]:
    """Return a counter-clockwise DXF ARC/CIRCLE course with exact extrema."""
    for value, name in (
        (center_x, "ARC center X"),
        (center_y, "ARC center Y"),
        (radius, "ARC radius"),
        (start_degrees, "ARC start angle"),
        (end_degrees, "ARC end angle"),
    ):
        _require_finite(value, name)
    if radius < 0.0:
        raise ValueError("negative ARC radius")
    if end_degrees < start_degrees:
        # DXF ARC angles are conventionally normalized, but malformed input
        # may carry many negative turns.  Modulo normalizes that in constant
        # time; a repeated ``while`` could otherwise become unbounded before
        # the reader has a chance to fail or report the drawing.
        end_degrees = start_degrees + (end_degrees - start_degrees) % 360.0
    return _swept_arc_points(
        center_x,
        center_y,
        radius,
        start_degrees,
        end_degrees - start_degrees,
        max_step_degrees,
        additional_extrema_degrees,
    )


def bulge_arc_points(
    start: Point,
    end: Point,
    bulge: float,
    additional_extrema_degrees: Sequence[float] = (),
) -> list[Point]:
    """Expand one DXF polyline bulge segment without replacing it by a chord.

    DXF stores the bulge at the starting vertex as ``tan(sweep / 4)``.  The
    signed sweep retains the prescribed clockwise/counter-clockwise course;
    cardinal points are injected by :func:`_swept_arc_points`, so the physical
    horizontal envelope is not phase-dependent on the sampling step.
    """
    for value, name in (
        (start[0], "bulge start X"),
        (start[1], "bulge start Y"),
        (end[0], "bulge end X"),
        (end[1], "bulge end Y"),
        (bulge, "bulge"),
    ):
        _require_finite(value, name)
    if abs(bulge) > MAX_SUPPORTED_ABS_BULGE:
        raise ValueError(
            f"bulge magnitude exceeds supported limit {MAX_SUPPORTED_ABS_BULGE:g}"
        )
    if abs(bulge) <= 1e-12:
        return [start, end]
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    chord = hypot(dx, dy)
    if chord <= 1e-12:
        return [start, end]
    # Unit normal to the chord's left side.  This signed center offset is the
    # standard DXF bulge construction and works for arcs above/below 180 deg.
    left_normal_x = -dy / chord
    left_normal_y = dx / chord
    center_offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
    center_x = 0.5 * (start[0] + end[0]) + left_normal_x * center_offset
    center_y = 0.5 * (start[1] + end[1]) + left_normal_y * center_offset
    radius = hypot(start[0] - center_x, start[1] - center_y)
    start_degrees = degrees(atan2(start[1] - center_y, start[0] - center_x))
    sweep_degrees = degrees(4.0 * atan2(bulge, 1.0))
    return _swept_arc_points(
        center_x,
        center_y,
        radius,
        start_degrees,
        sweep_degrees,
        additional_extrema_degrees=additional_extrema_degrees,
    )


def polyline_points(
    vertices: Sequence[tuple[float, float, float]],
    *,
    closed: bool,
    additional_extrema_degrees: Sequence[float] = (),
) -> list[Point]:
    """Materialize one DXF polyline vertex course, including bulge arcs."""
    for x, y, bulge in vertices:
        _require_finite(x, "polyline vertex X")
        _require_finite(y, "polyline vertex Y")
        _require_finite(bulge, "bulge")
    if len(vertices) < 2:
        return [(vertex[0], vertex[1]) for vertex in vertices]
    segment_count = len(vertices) if closed else len(vertices) - 1
    result: list[Point] = []
    for index in range(segment_count):
        x0, y0, bulge = vertices[index]
        x1, y1, _ = vertices[(index + 1) % len(vertices)]
        course = bulge_arc_points(
            (x0, y0),
            (x1, y1),
            bulge,
            additional_extrema_degrees,
        )
        if result:
            course = course[1:]
        result.extend(course)
    return result
