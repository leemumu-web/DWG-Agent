from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .manufacturing_ir import (
    BoxWeldAllowanceContract,
    ContourSegmentIR,
    PhysicalPlateIR,
    PhysicalPlateRole,
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
        (end_x, end_y, start_x, start_y, -bulge)
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


def _stretched_plate(plate: PhysicalPlateIR) -> PhysicalPlateIR | None:
    contract = plate.weld_allowance_contract
    if contract is None:
        return None
    try:
        stretched = stretch_outer_segments(plate.outer_segments, contract)
    except BoxWeldAllowanceProcessingError:
        return None
    return replace(plate, outer_segments=stretched)


def allowance_group_contract(
    group: PlateOutputGroup,
) -> BoxWeldAllowanceContract | None:
    """Authorize one grouped result only if growth preserves full equivalence."""

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
        > 1e-6
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
            tolerance=group.equivalence_tolerance_mm,
        )
        for other in materialized[1:]
    ):
        return None
    return representative


def group_equivalent_plate_pairs(
    plates: Iterable[PhysicalPlateIR],
    *,
    tolerance_mm: float = 1e-5,
) -> tuple[PlateOutputGroup, ...]:
    """Merge only the two physical BOX pairs after complete equivalence."""

    materialized = tuple(plates)
    by_role = {plate.role: plate for plate in materialized}
    if len(by_role) != 4 or set(by_role) != set(PhysicalPlateRole):
        raise ValueError("equivalence grouping requires all four physical BOX roles")
    pairs = (
        (PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT),
        (PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM),
    )
    groups: list[PlateOutputGroup] = []
    for first_role, second_role in pairs:
        first = by_role[first_role]
        second = by_role[second_role]
        if plates_equivalent(first, second, tolerance=tolerance_mm):
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
            continue
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
