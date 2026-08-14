"""Curve-aware whole-drawing external oracle for BOX manufacturing output."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from math import atan2, degrees, hypot

from shapely import affinity
from shapely.geometry import Point, Polygon

from steel_dxf_split.box.equivalence import PlateOutputGroup
from steel_dxf_split.box.manufacturing_ir import (
    PhysicalPlateRole,
    contour_polygon,
)

from .manual_reference import ManualPlate, ManualReference


@dataclass(frozen=True, slots=True)
class ComparisonTolerance:
    contour_hausdorff_mm: float = 3.1
    symmetric_difference_fraction: float = 0.002
    area_relative: float = 0.002
    zero_area_overlay_fraction: float = 0.0001
    hole_center_mm: float = 0.1
    hole_radius_mm: float = 0.01
    inner_contour_hausdorff_mm: float = 0.1
    inner_contour_symmetric_fraction: float = 0.001


DEFAULT_COMPARISON_TOLERANCE = ComparisonTolerance()


@dataclass(frozen=True, slots=True)
class PlateComparison:
    output_group: str
    manual_label: str
    family: str
    checks: tuple[tuple[str, bool], ...]
    metrics: tuple[tuple[str, float], ...]

    @property
    def ok(self) -> bool:
        return all(value for _, value in self.checks)

    @property
    def failed_check_keys(self) -> tuple[str, ...]:
        return tuple(key for key, value in self.checks if not value)


@dataclass(frozen=True, slots=True)
class WholeDrawingComparison:
    ok: bool
    comparisons: tuple[PlateComparison, ...]
    failed_checks: tuple[str, ...]
    failed_check_keys: tuple[str, ...]
    evidence_warnings: tuple[str, ...]
    internal_disposition: str | None


@dataclass(frozen=True, slots=True)
class _FramedGeometry:
    polygon: Polygon
    circular: tuple[tuple[float, float, float], ...]
    inner: tuple[Polygon, ...]


def _long_axis_angle(polygon: Polygon) -> float:
    coordinates = tuple(polygon.minimum_rotated_rectangle.exterior.coords)
    edges = tuple(
        (end[0] - start[0], end[1] - start[1])
        for start, end in zip(coordinates, coordinates[1:], strict=False)
    )
    dx, dy = max(edges, key=lambda item: hypot(item[0], item[1]))
    return degrees(atan2(dy, dx))


def _frame(
    polygon: Polygon,
    circular: tuple[tuple[float, float, float], ...],
    inner: tuple[Polygon, ...],
) -> _FramedGeometry:
    angle = _long_axis_angle(polygon)
    rotated = affinity.rotate(polygon, -angle, origin=(0.0, 0.0))
    center = rotated.centroid
    framed = affinity.translate(rotated, xoff=-center.x, yoff=-center.y)

    def transform_point(x: float, y: float) -> tuple[float, float]:
        transformed = affinity.rotate(Point(x, y), -angle, origin=(0.0, 0.0))
        return (float(transformed.x - center.x), float(transformed.y - center.y))

    return _FramedGeometry(
        polygon=framed,
        circular=tuple((*transform_point(x, y), radius) for x, y, radius in circular),
        inner=tuple(
            affinity.translate(
                affinity.rotate(item, -angle, origin=(0.0, 0.0)),
                xoff=-center.x,
                yoff=-center.y,
            )
            for item in inner
        ),
    )


def _variants(value: _FramedGeometry):
    for x_factor, y_factor in (
        (1.0, 1.0),
        (-1.0, 1.0),
        (1.0, -1.0),
        (-1.0, -1.0),
    ):
        yield _FramedGeometry(
            polygon=affinity.scale(
                value.polygon,
                xfact=x_factor,
                yfact=y_factor,
                origin=(0.0, 0.0),
            ),
            circular=tuple(
                (x_factor * x, y_factor * y, radius)
                for x, y, radius in value.circular
            ),
            inner=tuple(
                affinity.scale(
                    item,
                    xfact=x_factor,
                    yfact=y_factor,
                    origin=(0.0, 0.0),
                )
                for item in value.inner
            ),
        )


def _family(group: PlateOutputGroup) -> str:
    return (
        "web"
        if group.roles[0] in {PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT}
        else "flange"
    )


def _role(group: PlateOutputGroup) -> tuple[str, str | None, int]:
    if group.roles == (PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT):
        return ("腹", None, 2)
    if group.roles == (PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM):
        return ("翼", None, 2)
    role = {
        PhysicalPlateRole.WEB_LEFT: ("上腹", "top"),
        PhysicalPlateRole.WEB_RIGHT: ("下腹", "bottom"),
        PhysicalPlateRole.FLANGE_TOP: ("上翼", "top"),
        PhysicalPlateRole.FLANGE_BOTTOM: ("下翼", "bottom"),
    }[group.roles[0]]
    return (role[0], role[1], 1)


def _manual_geometry(manual: ManualPlate) -> _FramedGeometry:
    circular = tuple(
        (opening.center[0], opening.center[1], float(opening.radius))
        for opening in manual.openings
        if opening.kind == "CIRCLE" and opening.radius is not None
    )
    inner = tuple(
        opening.shape.polygon
        for opening in manual.openings
        if opening.kind == "POLYGON" and opening.shape is not None
    )
    return _frame(manual.shape.polygon, circular, inner)


def _output_geometry(group: PlateOutputGroup) -> _FramedGeometry:
    plate = group.representative
    circular = tuple((*cut.center, cut.radius_mm) for cut in plate.circular_cuts)
    inner = tuple(contour_polygon(item.segments) for item in plate.inner_contours)
    return _frame(contour_polygon(plate.outer_segments), circular, inner)


def _point_set_hausdorff(
    first: tuple[tuple[float, float], ...],
    second: tuple[tuple[float, float], ...],
) -> float:
    if not first and not second:
        return 0.0
    if not first or not second:
        return float("inf")

    def directed(sources, targets) -> float:
        return max(
            min(hypot(x - tx, y - ty) for tx, ty in targets)
            for x, y in sources
        )

    return max(directed(first, second), directed(second, first))


def _inner_distance(first: tuple[Polygon, ...], second: tuple[Polygon, ...]) -> tuple[float, float]:
    if not first and not second:
        return (0.0, 0.0)
    if len(first) != len(second):
        return (float("inf"), float("inf"))
    best: tuple[float, float] | None = None
    for ordered in permutations(second):
        hausdorff = max(
            left.hausdorff_distance(right)
            for left, right in zip(first, ordered, strict=True)
        )
        symmetric = max(
            left.symmetric_difference(right).area
            / max(left.area, right.area, 1.0)
            for left, right in zip(first, ordered, strict=True)
        )
        rank = (float(hausdorff), float(symmetric))
        if best is None or rank < best:
            best = rank
    assert best is not None
    return best


def _compare_plate(
    group: PlateOutputGroup,
    manual: ManualPlate,
    tolerance: ComparisonTolerance,
) -> PlateComparison:
    output = _output_geometry(group)
    target = _manual_geometry(manual)
    best: tuple[tuple[float, ...], _FramedGeometry, dict[str, float]] | None = None
    for variant in _variants(output):
        contour_hausdorff = float(variant.polygon.hausdorff_distance(target.polygon))
        symmetric = float(
            variant.polygon.symmetric_difference(target.polygon).area
            / max(variant.polygon.area, target.polygon.area, 1.0)
        )
        area_relative = float(
            abs(variant.polygon.area - target.polygon.area)
            / max(variant.polygon.area, target.polygon.area, 1.0)
        )
        output_centers = tuple((x, y) for x, y, _ in variant.circular)
        target_centers = tuple((x, y) for x, y, _ in target.circular)
        center_distance = _point_set_hausdorff(output_centers, target_centers)
        inner_hausdorff, inner_symmetric = _inner_distance(variant.inner, target.inner)
        metrics = {
            "contour_hausdorff_mm": contour_hausdorff,
            "symmetric_difference_fraction": symmetric,
            "area_relative": area_relative,
            "hole_center_hausdorff_mm": center_distance,
            "inner_contour_hausdorff_mm": inner_hausdorff,
            "inner_contour_symmetric_fraction": inner_symmetric,
        }
        contour_ok = (
            contour_hausdorff <= tolerance.contour_hausdorff_mm
            and symmetric <= tolerance.symmetric_difference_fraction
            and area_relative <= tolerance.area_relative
        ) or (
            symmetric <= tolerance.zero_area_overlay_fraction
            and area_relative <= tolerance.zero_area_overlay_fraction
        )
        rank = (
            not contour_ok,
            center_distance,
            inner_hausdorff,
            inner_symmetric,
            contour_hausdorff,
            symmetric,
            area_relative,
        )
        if best is None or rank < best[0]:
            best = (rank, variant, metrics)
    assert best is not None
    variant = best[1]
    metrics = best[2]
    output_radii = sorted(radius for _, _, radius in variant.circular)
    target_radii = sorted(radius for _, _, radius in target.circular)
    radius_error = (
        max(
            (abs(first - second) for first, second in zip(output_radii, target_radii, strict=True)),
            default=0.0,
        )
        if len(output_radii) == len(target_radii)
        else float("inf")
    )
    metrics["hole_radius_max_error_mm"] = radius_error
    expected_role, expected_side, expected_quantity = _role(group)
    contour_ok = (
        metrics["contour_hausdorff_mm"] <= tolerance.contour_hausdorff_mm
        and metrics["symmetric_difference_fraction"]
        <= tolerance.symmetric_difference_fraction
        and metrics["area_relative"] <= tolerance.area_relative
    ) or (
        metrics["symmetric_difference_fraction"]
        <= tolerance.zero_area_overlay_fraction
        and metrics["area_relative"] <= tolerance.zero_area_overlay_fraction
    )
    checks = (
        ("family", _family(group) == manual.family),
        ("role", manual.label == expected_role and manual.side == expected_side),
        ("quantity", group.quantity == manual.quantity == expected_quantity),
        ("contour", contour_ok),
        ("circular_hole_count", len(variant.circular) == len(target.circular)),
        (
            "circular_hole_centers",
            metrics["hole_center_hausdorff_mm"] <= tolerance.hole_center_mm,
        ),
        ("circular_hole_radii", radius_error <= tolerance.hole_radius_mm),
        ("inner_contour_count", len(variant.inner) == len(target.inner)),
        (
            "inner_contour_geometry",
            metrics["inner_contour_hausdorff_mm"]
            <= tolerance.inner_contour_hausdorff_mm
            and metrics["inner_contour_symmetric_fraction"]
            <= tolerance.inner_contour_symmetric_fraction,
        ),
    )
    return PlateComparison(
        output_group=group.group_id,
        manual_label=manual.label,
        family=manual.family,
        checks=checks,
        metrics=tuple(metrics.items()),
    )


def _best_family_matching(
    groups: tuple[PlateOutputGroup, ...],
    manuals: tuple[ManualPlate, ...],
    tolerance: ComparisonTolerance,
) -> tuple[PlateComparison, ...]:
    if len(groups) != len(manuals):
        return ()
    best: tuple[tuple[int, float], tuple[PlateComparison, ...]] | None = None
    for ordered in permutations(manuals):
        comparisons = tuple(
            _compare_plate(group, manual, tolerance)
            for group, manual in zip(groups, ordered, strict=True)
        )
        failed = sum(len(item.failed_check_keys) for item in comparisons)
        contour_sum = sum(dict(item.metrics)["contour_hausdorff_mm"] for item in comparisons)
        rank = (failed, contour_sum)
        if best is None or rank < best[0]:
            best = (rank, comparisons)
    assert best is not None
    return best[1]


def _expanded_groups_for_manuals(
    groups: tuple[PlateOutputGroup, ...],
    manuals: tuple[ManualPlate, ...],
) -> tuple[PlateOutputGroup, ...]:
    """Expose physical roles when a reference deliberately keeps them separate."""

    if len(groups) == len(manuals):
        return groups
    if not manuals or any(manual.quantity != 1 for manual in manuals):
        return groups
    if sum(group.quantity for group in groups) != len(manuals):
        return groups
    expanded: list[PlateOutputGroup] = []
    for group in groups:
        if group.quantity == 1:
            expanded.append(group)
            continue
        if group.quantity != len(group.physical_plates):
            return groups
        expanded.extend(
            PlateOutputGroup(
                group_id=f"{group.group_id}:{plate.role.value}",
                roles=(plate.role,),
                physical_plates=(plate,),
                representative=plate,
                quantity=1,
                merge_authorized=False,
                equivalence_tolerance_mm=group.equivalence_tolerance_mm,
            )
            for plate in group.physical_plates
        )
    return tuple(expanded)


def compare_groups_to_reference(
    groups: tuple[PlateOutputGroup, ...],
    reference: ManualReference,
    *,
    part_number: str,
    internal_disposition: str | None = None,
    tolerance: ComparisonTolerance = DEFAULT_COMPARISON_TOLERANCE,
) -> WholeDrawingComparison:
    matched_group_count = 0
    comparisons: tuple[PlateComparison, ...] = ()
    for family in ("web", "flange"):
        family_groups = tuple(group for group in groups if _family(group) == family)
        family_manuals = tuple(
            plate for plate in reference.plates if plate.family == family
        )
        matching_groups = _expanded_groups_for_manuals(
            family_groups,
            family_manuals,
        )
        matched_group_count += len(matching_groups)
        comparisons += _best_family_matching(
            matching_groups,
            family_manuals,
            tolerance,
        )
    failed_checks: list[str] = []
    failed_keys: list[str] = []
    for family in ("web", "flange"):
        output_count = sum(_family(group) == family for group in groups)
        reference_count = sum(plate.family == family for plate in reference.plates)
        if output_count != reference_count:
            failed_checks.append(
                f"{family} group count mismatch: output={output_count}, reference={reference_count}"
            )
            failed_keys.append(f"{family}_group_count")
    if reference.member_mark != part_number:
        failed_checks.append(
            f"member mark mismatch: output={part_number!r}, reference={reference.member_mark!r}"
        )
        failed_keys.append("member_mark")
    for item in comparisons:
        for key in item.failed_check_keys:
            failed_checks.append(f"{item.output_group}: {key}")
            failed_keys.append(key)
    if (
        len(comparisons) != matched_group_count
        or len(comparisons) != len(reference.plates)
    ):
        failed_checks.append("whole drawing plate matching is incomplete")
        failed_keys.append("plate_matching_complete")
    return WholeDrawingComparison(
        ok=not failed_checks,
        comparisons=comparisons,
        failed_checks=tuple(failed_checks),
        failed_check_keys=tuple(dict.fromkeys(failed_keys)),
        evidence_warnings=reference.evidence_warnings,
        internal_disposition=internal_disposition,
    )
