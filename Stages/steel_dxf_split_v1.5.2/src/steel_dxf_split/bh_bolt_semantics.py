from __future__ import annotations

from collections.abc import Iterable

from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize


def polygonize_closed_bolt_linework(
    linework: Iterable[LineString],
) -> list[Polygon]:
    """Return complete cut loops while ignoring dangling center/helper lines."""

    candidates = [
        polygon
        for polygon in polygonize(list(linework))
        if polygon.is_valid and polygon.area > 1e-6
    ]
    # A genuine opening is an independent ring.  Polygons that share a
    # boundary segment are cells created by a chord/grid, not independent cut
    # contours; reject the whole connected cell instead of emitting fragments.
    return [
        polygon
        for index, polygon in enumerate(candidates)
        if not any(
            index != other_index
            and polygon.boundary.intersection(other.boundary).length > 1e-6
            for other_index, other in enumerate(candidates)
        )
    ]


def opening_nominal_width(polygon: Polygon) -> float:
    """Return the rotation-invariant short envelope dimension of an opening."""

    rectangle = polygon.minimum_rotated_rectangle
    coordinates = list(rectangle.exterior.coords)
    lengths = [
        LineString((start, end)).length
        for start, end in zip(coordinates, coordinates[1:])
        if start != end
    ]
    return min(lengths, default=0.0)
