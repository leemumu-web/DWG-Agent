from __future__ import annotations

from dataclasses import dataclass
from math import hypot, isfinite
from typing import Sequence


RED_ACI = 1
WHITE_ACI = 7


@dataclass(frozen=True, slots=True)
class SymmetricHoleColorPlan:
    colors_aci: tuple[int, ...]
    pairs: tuple[tuple[int, int], ...]
    ambiguous_indices: tuple[int, ...]
    midline_indices: tuple[int, ...]


def plan_symmetric_circle_colors(
    holes: Sequence[tuple[float, float, float]],
    *,
    plate_min_x_mm: float,
    plate_max_x_mm: float,
    center_tolerance_mm: float = 0.01,
    radius_tolerance_mm: float = 0.01,
    midline_tolerance_mm: float = 0.01,
) -> SymmetricHoleColorPlan:
    if (
        not isfinite(plate_min_x_mm)
        or not isfinite(plate_max_x_mm)
        or plate_max_x_mm <= plate_min_x_mm
    ):
        raise ValueError("plate X bounds must be finite and have positive width")
    for x, y, radius in holes:
        if not all(isfinite(value) for value in (x, y, radius)) or radius <= 0.0:
            raise ValueError("circle coordinates and radius must be finite and positive")

    mid_x = (plate_min_x_mm + plate_max_x_mm) / 2.0
    left_indices = tuple(
        index
        for index, (x, _, _) in enumerate(holes)
        if x < mid_x - midline_tolerance_mm
    )
    right_indices = tuple(
        index
        for index, (x, _, _) in enumerate(holes)
        if x > mid_x + midline_tolerance_mm
    )
    midline_indices = tuple(
        index
        for index in range(len(holes))
        if index not in left_indices and index not in right_indices
    )

    candidates_by_left: dict[int, tuple[int, ...]] = {}
    for left_index in left_indices:
        left_x, left_y, left_radius = holes[left_index]
        target_x = 2.0 * mid_x - left_x
        candidates_by_left[left_index] = tuple(
            right_index
            for right_index in right_indices
            if hypot(
                holes[right_index][0] - target_x,
                holes[right_index][1] - left_y,
            )
            <= center_tolerance_mm
            and abs(holes[right_index][2] - left_radius) <= radius_tolerance_mm
        )
    candidates_by_right = {
        right_index: tuple(
            left_index
            for left_index, candidates in candidates_by_left.items()
            if right_index in candidates
        )
        for right_index in right_indices
    }
    pairs = tuple(
        sorted(
            (
                (left_index, candidates[0])
                for left_index, candidates in candidates_by_left.items()
                if len(candidates) == 1
                and len(candidates_by_right[candidates[0]]) == 1
            ),
            key=lambda pair: (holes[pair[0]], holes[pair[1]]),
        )
    )
    ambiguous_indices: set[int] = set()
    for left_index, right_candidates in candidates_by_left.items():
        for right_index in right_candidates:
            if (
                len(right_candidates) != 1
                or len(candidates_by_right[right_index]) != 1
            ):
                ambiguous_indices.update((left_index, right_index))
    colors = [WHITE_ACI] * len(holes)
    for left_index, _ in pairs:
        colors[left_index] = RED_ACI

    return SymmetricHoleColorPlan(
        colors_aci=tuple(colors),
        pairs=pairs,
        ambiguous_indices=tuple(sorted(ambiguous_indices)),
        midline_indices=midline_indices,
    )
