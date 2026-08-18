from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from hashlib import sha256
from math import hypot

from shapely.affinity import affine_transform, translate
from shapely.geometry import Polygon

from .manufacturing_ir import (
    BoxWeldAllowanceContract,
    ContourSegmentIR,
    EvidenceState,
    PhysicalPlateIR,
    PhysicalPlateRole,
    contour_polygon,
)
from .weld_allowance import (
    BoxWeldAllowanceProcessingError,
    stretch_outer_segments,
)


@dataclass(frozen=True, slots=True)
class PlateOutputGroup:
    group_id: str
    roles: tuple[PhysicalPlateRole, ...]
    physical_plates: tuple[PhysicalPlateIR, ...]
    representative: PhysicalPlateIR
    quantity: int
    merge_authorized: bool
    equivalence_tolerance_mm: float


BOX_DRAFTING_RESOLUTION_MM = 0.05
BOX_WEB_MANUFACTURING_EQUIVALENCE_MM = 1.0


_TRANSFORMS = (
    (1.0, 0.0, 0.0, 1.0),
    (-1.0, 0.0, 0.0, 1.0),
    (1.0, 0.0, 0.0, -1.0),
    (-1.0, 0.0, 0.0, -1.0),
    (0.0, 1.0, 1.0, 0.0),
    (0.0, -1.0, 1.0, 0.0),
    (0.0, 1.0, -1.0, 0.0),
    (0.0, -1.0, -1.0, 0.0),
)


def _transform_point(
    point: tuple[float, float],
    transform: tuple[float, float, float, float],
) -> tuple[float, float]:
    a, b, c, d = transform
    return (a * point[0] + b * point[1], c * point[0] + d * point[1])


def _determinant(transform: tuple[float, float, float, float]) -> float:
    a, b, c, d = transform
    return a * d - b * c


def _round(value: float, tolerance: float) -> float:
    quantized = round(value / tolerance) * tolerance
    rounded = round(quantized, 9)
    return 0.0 if rounded == -0.0 else rounded


def _canonical_loop(
    segments: tuple[tuple[float, float, float, float, float], ...],
) -> tuple[tuple[float, float, float, float, float], ...]:
    if not segments:
        return ()
    forward = segments
    reverse = tuple(
        (
            end_x,
            end_y,
            start_x,
            start_y,
            0.0 if bulge == 0.0 else -bulge,
        )
        for start_x, start_y, end_x, end_y, bulge in reversed(segments)
    )
    variants = tuple(
        values[index:] + values[:index]
        for values in (forward, reverse)
        for index in range(len(values))
    )
    return min(variants)


def _geometry_signature(
    plate: PhysicalPlateIR,
    transform: tuple[float, float, float, float],
    tolerance: float,
) -> str:
    transformed_outer = tuple(
        _transform_point(point, transform)
        for segment in plate.outer_segments
        for point in (segment.start, segment.end)
    )
    min_x = min(point[0] for point in transformed_outer)
    min_y = min(point[1] for point in transformed_outer)
    determinant = _determinant(transform)

    def point(value: tuple[float, float]) -> tuple[float, float]:
        x, y = _transform_point(value, transform)
        return (_round(x - min_x, tolerance), _round(y - min_y, tolerance))

    def loop(
        segments: Iterable[ContourSegmentIR],
    ) -> tuple[tuple[float, float, float, float, float], ...]:
        payload = []
        for segment in segments:
            start = point(segment.start)
            end = point(segment.end)
            payload.append(
                (
                    start[0],
                    start[1],
                    end[0],
                    end[1],
                    _round(segment.bulge * determinant, tolerance),
                )
            )
        return _canonical_loop(tuple(payload))

    circular = sorted(
        (
            *point(cut.center),
            _round(cut.radius_mm, tolerance),
        )
        for cut in plate.circular_cuts
    )
    inner = sorted(loop(contour.segments) for contour in plate.inner_contours)
    payload = {
        "outer": loop(plate.outer_segments),
        "circular": circular,
        "inner": inner,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def plates_equivalent(
    first: PhysicalPlateIR,
    second: PhysicalPlateIR,
    *,
    tolerance: float,
) -> bool:
    if first.material != second.material:
        return False
    if abs(first.thickness_mm - second.thickness_mm) > tolerance:
        return False
    first_signatures = {
        _geometry_signature(first, transform, tolerance) for transform in _TRANSFORMS
    }
    second_signatures = {
        _geometry_signature(second, transform, tolerance) for transform in _TRANSFORMS
    }
    return not first_signatures.isdisjoint(second_signatures)


def plate_manufacturing_key(
    plate: PhysicalPlateIR,
    *,
    tolerance: float = 1e-5,
) -> str:
    """Return the complete role-neutral manufacturing identity of one plate.

    Source identifiers, proof wording, plate identifiers, and physical-role names
    are deliberately excluded.  Material, thickness, the complete outer contour,
    every circular/non-circular opening, and the effective weld allowance remain.
    """

    if tolerance <= 0.0:
        raise ValueError("manufacturing-key tolerance must be positive")
    effective_plate = _effective_manufacturing_plate(plate)
    geometry = min(
        _geometry_signature(effective_plate, transform, tolerance)
        for transform in _TRANSFORMS
    )
    contract = plate.weld_allowance_contract
    allowance = (
        None
        if contract is None
        else {
            "schema_version": contract.schema_version,
            "coordinate_unit": contract.coordinate_unit,
            "longitudinal_axis": contract.longitudinal_axis,
            "horizontal_residual_mm": _round(
                contract.horizontal_residual_mm, tolerance
            ),
            "main_length_mm": _round(contract.main_length_mm, tolerance),
            "allowance_mm": _round(contract.allowance_mm, tolerance),
            "stationary_end": contract.stationary_end,
            "movable_end": contract.movable_end,
        }
    )
    payload = {
        "material": plate.material,
        "thickness_mm": _round(plate.thickness_mm, tolerance),
        "geometry": json.loads(geometry),
        "weld_allowance": allowance,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _normalized_point(
    point: tuple[float, float],
    transform: tuple[float, float, float, float],
    origin: tuple[float, float],
) -> tuple[float, float]:
    a, b, c, d = transform
    x, y = point
    return (a * x + b * y - origin[0], c * x + d * y - origin[1])


def _polygons_within_tolerance(
    first: Polygon,
    second: Polygon,
    tolerance: float,
) -> bool:
    return (
        first.boundary.hausdorff_distance(second.boundary) <= tolerance
        and first.symmetric_difference(second).area
        <= tolerance * max(first.length, second.length, 1.0)
    )


def _has_perfect_matching(adjacency: tuple[tuple[bool, ...], ...]) -> bool:
    if any(not any(row) for row in adjacency):
        return False
    matched_left_by_right: dict[int, int] = {}

    def assign(left: int, seen_right: set[int]) -> bool:
        for right, allowed in enumerate(adjacency[left]):
            if not allowed or right in seen_right:
                continue
            seen_right.add(right)
            current = matched_left_by_right.get(right)
            if current is None or assign(current, seen_right):
                matched_left_by_right[right] = left
                return True
        return False

    return all(assign(left, set()) for left in range(len(adjacency)))


def _allowance_contracts_equivalent(
    first: BoxWeldAllowanceContract | None,
    second: BoxWeldAllowanceContract | None,
    tolerance: float,
) -> bool:
    if first is None or second is None:
        return first is second
    return (
        first.schema_version == second.schema_version
        and first.coordinate_unit == second.coordinate_unit
        and first.longitudinal_axis == second.longitudinal_axis
        and first.stationary_end == second.stationary_end
        and first.movable_end == second.movable_end
        and abs(first.horizontal_residual_mm - second.horizontal_residual_mm)
        <= tolerance
        and abs(first.main_length_mm - second.main_length_mm) <= tolerance
        and abs(first.allowance_mm - second.allowance_mm) <= tolerance
    )


def _geometry_matches_under_transform(
    first: PhysicalPlateIR,
    second: PhysicalPlateIR,
    transform: tuple[float, float, float, float],
    tolerance: float,
) -> bool:
    first_outer = contour_polygon(first.outer_segments)
    second_outer_raw = contour_polygon(second.outer_segments)
    a, b, c, d = transform
    second_transformed = affine_transform(
        second_outer_raw,
        (a, b, c, d, 0.0, 0.0),
    )
    first_min_x, first_min_y, _first_max_x, _first_max_y = first_outer.bounds
    second_min_x, second_min_y, _second_max_x, _second_max_y = (
        second_transformed.bounds
    )
    first_normalized = translate(first_outer, xoff=-first_min_x, yoff=-first_min_y)
    second_normalized = translate(
        second_transformed,
        xoff=-second_min_x,
        yoff=-second_min_y,
    )
    if not _polygons_within_tolerance(
        first_normalized,
        second_normalized,
        tolerance,
    ):
        return False

    if len(first.circular_cuts) != len(second.circular_cuts):
        return False
    first_centers = tuple(
        (
            cut.center[0] - first_min_x,
            cut.center[1] - first_min_y,
            cut.radius_mm,
        )
        for cut in first.circular_cuts
    )
    second_centers = tuple(
        (
            *_normalized_point(
                cut.center,
                transform,
                (second_min_x, second_min_y),
            ),
            cut.radius_mm,
        )
        for cut in second.circular_cuts
    )
    circular_adjacency = tuple(
        tuple(
            hypot(left[0] - right[0], left[1] - right[1]) <= tolerance
            and abs(left[2] - right[2]) <= tolerance
            for right in second_centers
        )
        for left in first_centers
    )
    if circular_adjacency and not _has_perfect_matching(circular_adjacency):
        return False

    if len(first.inner_contours) != len(second.inner_contours):
        return False
    first_inner = tuple(
        translate(
            contour_polygon(contour.segments),
            xoff=-first_min_x,
            yoff=-first_min_y,
        )
        for contour in first.inner_contours
    )
    second_inner = tuple(
        translate(
            affine_transform(
                contour_polygon(contour.segments),
                (a, b, c, d, 0.0, 0.0),
            ),
            xoff=-second_min_x,
            yoff=-second_min_y,
        )
        for contour in second.inner_contours
    )
    inner_adjacency = tuple(
        tuple(
            _polygons_within_tolerance(left, right, tolerance)
            for right in second_inner
        )
        for left in first_inner
    )
    return not inner_adjacency or _has_perfect_matching(inner_adjacency)


def plates_manufacturing_equivalent(
    first: PhysicalPlateIR,
    second: PhysicalPlateIR,
    *,
    tolerance: float,
) -> bool:
    """Compare actual role-neutral output geometry without quantization buckets."""

    if tolerance <= 0.0:
        raise ValueError("manufacturing-equivalence tolerance must be positive")
    if first.material != second.material:
        return False
    if abs(first.thickness_mm - second.thickness_mm) > tolerance:
        return False
    if not _allowance_contracts_equivalent(
        first.weld_allowance_contract,
        second.weld_allowance_contract,
        tolerance,
    ):
        return False
    effective_first = _effective_manufacturing_plate(first)
    effective_second = _effective_manufacturing_plate(second)
    return any(
        _geometry_matches_under_transform(
            effective_first,
            effective_second,
            transform,
            tolerance,
        )
        for transform in _TRANSFORMS
    )


def _stretched_plate(plate: PhysicalPlateIR) -> PhysicalPlateIR | None:
    contract = plate.weld_allowance_contract
    if contract is None:
        return None
    try:
        stretched = stretch_outer_segments(plate.outer_segments, contract)
    except BoxWeldAllowanceProcessingError:
        return None
    return replace(plate, outer_segments=stretched)


def _effective_manufacturing_plate(plate: PhysicalPlateIR) -> PhysicalPlateIR:
    if plate.weld_allowance_contract is None:
        return plate
    stretched = _stretched_plate(plate)
    if stretched is None:
        raise ValueError("plate weld allowance could not be applied")
    return stretched


def plates_equivalent_after_allowance(
    first: PhysicalPlateIR,
    second: PhysicalPlateIR,
    *,
    tolerance: float,
) -> bool:
    """Compare the actual manufacturing geometry after one-sided growth."""

    return plates_equivalent(
        _effective_manufacturing_plate(first),
        _effective_manufacturing_plate(second),
        tolerance=tolerance,
    )


def allowance_group_contract(
    group: PlateOutputGroup,
) -> BoxWeldAllowanceContract | None:
    """Authorize one grouped result only if growth preserves full equivalence."""

    comparison_tolerance = max(
        group.equivalence_tolerance_mm,
        BOX_DRAFTING_RESOLUTION_MM,
    )
    stretched = tuple(_stretched_plate(plate) for plate in group.physical_plates)
    if any(plate is None for plate in stretched):
        return None
    materialized = tuple(plate for plate in stretched if plate is not None)
    representative = group.representative.weld_allowance_contract
    if representative is None:
        return None
    if any(
        plate.weld_allowance_contract is None
        or abs(
            plate.weld_allowance_contract.main_length_mm
            - representative.main_length_mm
        )
        > comparison_tolerance
        or plate.weld_allowance_contract.allowance_mm
        != representative.allowance_mm
        for plate in group.physical_plates
    ):
        return None
    first = materialized[0]
    if any(
        not plates_equivalent(
            first,
            other,
            tolerance=comparison_tolerance,
        )
        for other in materialized[1:]
    ):
        return None
    return representative


def _source_group_id(source_id: str) -> str:
    group_id, separator, _ = source_id.rpartition("/")
    return group_id if separator else source_id


def _plate_internal_features_have_independent_direct_sources(
    plate: PhysicalPlateIR,
) -> bool:
    features = (*plate.circular_cuts, *plate.inner_contours)
    return all(
        feature.evidence.state is EvidenceState.DIRECT
        and len(
            {
                _source_group_id(source_id)
                for source_id in feature.evidence.source_ids
            }
        )
        >= 2
        for feature in features
    )


def group_equivalent_plate_pairs(
    plates: Iterable[PhysicalPlateIR],
    *,
    tolerance_mm: float = 1e-5,
) -> tuple[PlateOutputGroup, ...]:
    """Merge complete equivalent pairs with sufficient physical-source proof.

    A feature-free equivalent web pair needs no additional opening proof.  Every
    circular cut or inner contour carried by an equivalent web pair must instead
    have direct evidence from at least two independent source drawing groups.
    This prevents one legacy source opening copied to both webs from authorizing
    a quantity-2 output.  Flange grouping retains its complete-equivalence rule.
    """

    materialized = tuple(plates)
    by_role = {plate.role: plate for plate in materialized}
    if len(by_role) != 4 or set(by_role) != set(PhysicalPlateRole):
        raise ValueError("equivalence grouping requires all four physical BOX roles")
    groups: list[PlateOutputGroup] = []
    web_roles = (
        PhysicalPlateRole.WEB_LEFT,
        PhysicalPlateRole.WEB_RIGHT,
    )
    first_role, second_role = web_roles
    first = by_role[first_role]
    second = by_role[second_role]
    web_merge_authorized = (
        plates_manufacturing_equivalent(
            first,
            second,
            tolerance=max(tolerance_mm, BOX_WEB_MANUFACTURING_EQUIVALENCE_MM),
        )
        and _plate_internal_features_have_independent_direct_sources(first)
        and _plate_internal_features_have_independent_direct_sources(second)
    )
    if web_merge_authorized:
        groups.append(
            PlateOutputGroup(
                group_id=f"{first_role.value}+{second_role.value}",
                roles=web_roles,
                physical_plates=(first, second),
                representative=first,
                quantity=2,
                merge_authorized=True,
                equivalence_tolerance_mm=tolerance_mm,
            )
        )
    else:
        for role, plate in ((first_role, first), (second_role, second)):
            groups.append(
                PlateOutputGroup(
                    group_id=role.value,
                    roles=(role,),
                    physical_plates=(plate,),
                    representative=plate,
                    quantity=1,
                    merge_authorized=False,
                    equivalence_tolerance_mm=tolerance_mm,
                )
            )
    flange_roles = (
        PhysicalPlateRole.FLANGE_TOP,
        PhysicalPlateRole.FLANGE_BOTTOM,
    )
    first_role, second_role = flange_roles
    first = by_role[first_role]
    second = by_role[second_role]
    if plates_manufacturing_equivalent(
        first,
        second,
        tolerance=max(tolerance_mm, BOX_DRAFTING_RESOLUTION_MM),
    ):
        groups.append(
            PlateOutputGroup(
                group_id=f"{first_role.value}+{second_role.value}",
                roles=(first_role, second_role),
                physical_plates=(first, second),
                representative=first,
                quantity=2,
                merge_authorized=True,
                equivalence_tolerance_mm=tolerance_mm,
            )
        )
    else:
        for role, plate in ((first_role, first), (second_role, second)):
            groups.append(
                PlateOutputGroup(
                    group_id=role.value,
                    roles=(role,),
                    physical_plates=(plate,),
                    representative=plate,
                    quantity=1,
                    merge_authorized=False,
                    equivalence_tolerance_mm=tolerance_mm,
                )
            )
    return tuple(groups)
