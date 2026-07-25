from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from unicodedata import east_asian_width

from shapely import affinity
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import polylabel

MINIMUM_PART_MARK_HEIGHT_MM = 30.0
PART_MARK_CLEARANCE_MM = 5.0
STANDARD_PART_MARK_HEIGHTS_MM = (120.0, 90.0, 75.0, 60.0, 45.0, 30.0)


class PartMarkLayoutError(ValueError):
    """A proved material region cannot safely contain its part mark."""


@dataclass(frozen=True, slots=True)
class PartMarkTarget:
    target_id: str
    label: str
    outer_geometry: BaseGeometry
    material_geometry: BaseGeometry
    hole_count: int = 0


@dataclass(frozen=True, slots=True)
class PartMarkPlacement:
    target_id: str
    label: str
    point: tuple[float, float]
    height_mm: float


def label_em_width(value: str) -> float:
    """Estimate SimSun advance widths without requiring the font on the host."""

    return sum(
        1.0
        if east_asian_width(character) in {"W", "F"}
        else 0.6
        if character.isascii()
        else 0.8
        for character in value
    )


def part_mark_envelope(
    label: str,
    point: tuple[float, float],
    height: float,
) -> Polygon:
    """Return the actual axis-aligned SimSun text envelope."""

    half_width = label_em_width(label) * height / 2.0
    half_height = height / 2.0
    return box(
        point[0] - half_width,
        point[1] - half_height,
        point[0] + half_width,
        point[1] + half_height,
    )


def part_mark_clearance_envelope(
    label: str,
    point: tuple[float, float],
    height: float,
) -> Polygon:
    """Return the actual text envelope plus 5 mm clearance on every side."""

    envelope = part_mark_envelope(label, point, height)
    min_x, min_y, max_x, max_y = envelope.bounds
    return box(
        min_x - PART_MARK_CLEARANCE_MM,
        min_y - PART_MARK_CLEARANCE_MM,
        max_x + PART_MARK_CLEARANCE_MM,
        max_y + PART_MARK_CLEARANCE_MM,
    )


def preferred_standard_part_mark_height(capacity_mm: float) -> float:
    """Map a family's visual capacity to its preferred standard height."""

    for height in STANDARD_PART_MARK_HEIGHTS_MM:
        if height <= capacity_mm + 1e-9:
            return height
    return MINIMUM_PART_MARK_HEIGHT_MM


def _polygon_components(geometry: BaseGeometry) -> tuple[Polygon, ...]:
    if isinstance(geometry, Polygon):
        return (geometry,)
    if isinstance(geometry, MultiPolygon):
        return tuple(geometry.geoms)
    return tuple(
        component
        for component in getattr(geometry, "geoms", ())
        if isinstance(component, Polygon)
    )


def _anchor_candidates(
    target: PartMarkTarget,
    height_mm: float,
) -> tuple[Point, ...]:
    actual_width = label_em_width(target.label) * height_mm
    clearance_width = actual_width + 2.0 * PART_MARK_CLEARANCE_MM
    clearance_height = height_mm + 2.0 * PART_MARK_CLEARANCE_MM
    half_width = clearance_width / 2.0
    half_height = clearance_height / 2.0
    material = target.material_geometry
    candidates = [
        target.outer_geometry.centroid,
        material.centroid,
        material.representative_point(),
    ]

    normalized = affinity.scale(
        material,
        xfact=1.0 / half_width,
        yfact=1.0 / half_height,
        origin=(0.0, 0.0),
    )
    components = sorted(
        _polygon_components(normalized),
        key=lambda polygon: (-polygon.area, polygon.wkb_hex),
    )
    for component in components:
        normalized_point = polylabel(component, tolerance=0.001)
        candidates.append(
            Point(
                normalized_point.x * half_width,
                normalized_point.y * half_height,
            )
        )
    return tuple(candidates)


def _find_anchor(
    target: PartMarkTarget,
    height_mm: float,
) -> tuple[float, float] | None:
    material = target.material_geometry
    if material.is_empty or material.area <= 1e-6:
        return None
    for candidate in _anchor_candidates(target, height_mm):
        point = (float(candidate.x), float(candidate.y))
        if material.covers(
            part_mark_clearance_envelope(target.label, point, height_mm)
        ):
            return point
    return None


def _candidate_heights(preferred_height_mm: float) -> tuple[float, ...]:
    if not isfinite(preferred_height_mm) or preferred_height_mm <= 0.0:
        raise ValueError("Preferred part-mark height must be a positive finite value.")
    preferred = max(MINIMUM_PART_MARK_HEIGHT_MM, float(preferred_height_mm))
    candidates = [preferred]
    candidates.extend(
        height
        for height in STANDARD_PART_MARK_HEIGHTS_MM
        if MINIMUM_PART_MARK_HEIGHT_MM <= height < preferred - 1e-9
    )
    if candidates[-1] != MINIMUM_PART_MARK_HEIGHT_MM:
        candidates.append(MINIMUM_PART_MARK_HEIGHT_MM)
    return tuple(candidates)


def _minimum_fit_diagnostic(target: PartMarkTarget) -> str:
    height = MINIMUM_PART_MARK_HEIGHT_MM
    required_width = (
        label_em_width(target.label) * height + 2.0 * PART_MARK_CLEARANCE_MM
    )
    required_height = height + 2.0 * PART_MARK_CLEARANCE_MM
    if target.material_geometry.is_empty:
        material_width = 0.0
        material_height = 0.0
    else:
        min_x, min_y, max_x, max_y = target.material_geometry.bounds
        material_width = float(max_x - min_x)
        material_height = float(max_y - min_y)
    return (
        f"Part-mark target {target.target_id!r} cannot fit the minimum 30 mm "
        f"label clearance envelope: required={required_width:.3f} x "
        f"{required_height:.3f} mm; material bounds={material_width:.3f} x "
        f"{material_height:.3f} mm; hole_count={target.hole_count}."
    )


def layout_part_marks(
    targets: tuple[PartMarkTarget, ...],
    *,
    preferred_height_mm: float,
) -> tuple[PartMarkPlacement, ...]:
    """Place all targets at one shared height, never above the family preference."""

    if not targets:
        return ()
    for height in _candidate_heights(preferred_height_mm):
        points = tuple(_find_anchor(target, height) for target in targets)
        if all(point is not None for point in points):
            return tuple(
                PartMarkPlacement(
                    target_id=target.target_id,
                    label=target.label,
                    point=point,
                    height_mm=height,
                )
                for target, point in zip(targets, points, strict=True)
                if point is not None
            )

    failed = next(
        target
        for target in targets
        if _find_anchor(target, MINIMUM_PART_MARK_HEIGHT_MM) is None
    )
    raise PartMarkLayoutError(_minimum_fit_diagnostic(failed))
