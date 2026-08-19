"""Feature-preserving boundary growth shared by BH and BOX allowance paths."""

from __future__ import annotations

from dataclasses import replace
import math
from typing import Any


_TOLERANCE_MM = 1e-7


def cut_feature_x_extents(
    document: Any,
    *,
    owner_id: str | None = None,
) -> tuple[tuple[float, float], ...]:
    """Return conservative X extents for native CUT_HOLE features.

    When ``owner_id`` is supplied, only features bound to that physical plate
    are considered. This keeps a hole group on one plate from influencing the
    allowance insertion choice of another plate in the same DXF.
    """

    from ezdxf import bbox as dxf_bbox

    extents: list[tuple[float, float]] = []
    for entity in document.modelspace():
        if entity.dxf.get("layer", "") != "CUT_HOLE":
            continue
        if owner_id is not None:
            owner = None
            for app_id in ("STEEL_DXF_SPLIT", "BOX_DXF_SPLIT"):
                try:
                    tags = list(entity.get_xdata(app_id))
                except Exception:
                    continue
                if len(tags) >= 2:
                    owner = str(tags[1].value)
                    break
            if owner != owner_id:
                continue
        if entity.dxftype() == "CIRCLE":
            center = entity.dxf.center
            radius = abs(float(entity.dxf.radius))
            extents.append((float(center.x) - radius, float(center.x) + radius))
            continue
        bounds = dxf_bbox.extents([entity])
        if not bounds.has_data:
            continue
        extents.append((float(bounds.extmin.x), float(bounds.extmax.x)))
    return tuple(extents)


def _translated(segment: Any, *, start_shift: float, end_shift: float) -> Any:
    return replace(
        segment,
        start=(segment.start[0] + start_shift, segment.start[1]),
        end=(segment.end[0] + end_shift, segment.end[1]),
    )


def _terminal_translation(
    segments: tuple[Any, ...],
    contract: Any,
) -> tuple[Any, ...]:
    index_by_id = {
        segment.segment_id: index for index, segment in enumerate(segments)
    }
    terminal_indices = tuple(
        index_by_id[segment_id]
        for segment_id in contract.positive_terminal_segment_ids
    )
    movable_vertices = {
        vertex_index
        for segment_index in terminal_indices
        for vertex_index in (segment_index, (segment_index + 1) % len(segments))
    }
    return tuple(
        _translated(
            segment,
            start_shift=(
                contract.allowance_mm
                if index in movable_vertices
                else 0.0
            ),
            end_shift=(
                contract.allowance_mm
                if (index + 1) % len(segments) in movable_vertices
                else 0.0
            ),
        )
        for index, segment in enumerate(segments)
    )


def _safe_insertion_x(
    segments: tuple[Any, ...],
    contract: Any,
    feature_x_extents: tuple[tuple[float, float], ...],
) -> float | None:
    index_by_id = {
        segment.segment_id: index for index, segment in enumerate(segments)
    }
    rail_indices = tuple(
        index_by_id[segment_id] for segment_id in contract.rail_segment_ids
    )
    rails = tuple(segments[index] for index in rail_indices)
    if any(
        abs(segment.end[0] - segment.start[0]) <= _TOLERANCE_MM
        or abs(segment.bulge) > _TOLERANCE_MM
        for segment in rails
    ):
        return None
    overlap_min = max(
        min(segment.start[0], segment.end[0]) for segment in rails
    )
    overlap_max = min(
        max(segment.start[0], segment.end[0]) for segment in rails
    )
    allowance = float(contract.allowance_mm)
    if overlap_max - overlap_min <= allowance + _TOLERANCE_MM:
        return None

    intervals = sorted(
        (
            min(float(start), float(end)),
            max(float(start), float(end)),
        )
        for start, end in feature_x_extents
        if math.isfinite(float(start))
        and math.isfinite(float(end))
        and max(float(start), float(end)) > overlap_min + _TOLERANCE_MM
        and min(float(start), float(end)) < overlap_max - _TOLERANCE_MM
    )
    gaps: list[tuple[float, float]] = []
    cursor = overlap_min
    for start, end in intervals:
        start = max(start, overlap_min)
        end = min(end, overlap_max)
        if start > cursor + _TOLERANCE_MM:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if overlap_max > cursor + _TOLERANCE_MM:
        gaps.append((cursor, overlap_max))

    candidates = [
        (gap_start, gap_end)
        for gap_start, gap_end in gaps
        if gap_end - gap_start > allowance + _TOLERANCE_MM
    ]
    if not candidates:
        return None
    minimum_x = min(
        coordinate
        for segment in segments
        for coordinate in (segment.start[0], segment.end[0])
    )
    maximum_x = max(
        coordinate
        for segment in segments
        for coordinate in (segment.start[0], segment.end[0])
    )
    middle = (minimum_x + maximum_x) / 2.0
    feature_centers = tuple((start + end) / 2.0 for start, end in intervals)
    left_features = tuple(
        interval
        for interval, center in zip(intervals, feature_centers, strict=True)
        if center < middle - _TOLERANCE_MM
    )
    right_features = tuple(
        interval
        for interval, center in zip(intervals, feature_centers, strict=True)
        if center > middle + _TOLERANCE_MM
    )

    # Two-sided features must use an internal gap. This prevents a narrow gap
    # between adjacent holes on one side from winning merely because it is
    # close to the plate midpoint. For a single-sided feature group, require
    # the opposite end gap, which is the part farthest from the holes.
    if left_features and right_features:

        def is_between_sides(gap: tuple[float, float]) -> bool:
            left_candidates = [
                interval
                for interval in intervals
                if interval[1] <= gap[0] + _TOLERANCE_MM
            ]
            right_candidates = [
                interval
                for interval in intervals
                if interval[0] >= gap[1] - _TOLERANCE_MM
            ]
            if not left_candidates or not right_candidates:
                return False
            left = max(left_candidates, key=lambda interval: interval[1])
            right = min(right_candidates, key=lambda interval: interval[0])
            return (
                (left[0] + left[1]) / 2.0 < middle - _TOLERANCE_MM
                and (right[0] + right[1]) / 2.0 > middle + _TOLERANCE_MM
            )

        preferred = tuple(
            gap
            for gap in candidates
            if is_between_sides(gap)
        )
    elif left_features:
        far_end = max(end for _, end in intervals)
        preferred = tuple(
            gap for gap in candidates if gap[0] >= far_end - _TOLERANCE_MM
        )
    elif right_features:
        far_end = min(start for start, _ in intervals)
        preferred = tuple(
            gap for gap in candidates if gap[1] <= far_end + _TOLERANCE_MM
        )
    else:
        preferred = tuple(candidates)
    if not preferred:
        return None

    ranked = sorted(
        preferred,
        key=lambda gap: (
            abs((gap[0] + gap[1]) / 2.0 - middle),
            -(gap[1] - gap[0]),
            gap[0],
        ),
    )
    low, high = ranked[0]
    return (low + high - allowance) / 2.0


def stretch_boundary_segments(
    segments: tuple[Any, ...],
    contract: Any,
    *,
    feature_x_extents: tuple[tuple[float, float], ...] | None = None,
) -> tuple[Any, ...]:
    """Grow only the boundary, inserting material in a feature-free X gap.

    ``feature_x_extents=None`` deliberately preserves the legacy terminal-only
    transformation for direct callers that do not have a saved DXF. Production
    allowance paths pass an empty tuple or the actual CUT_HOLE extents, which
    enables the feature-free middle insertion policy.
    """

    if contract.allowance_mm == 0.0:
        return segments
    if feature_x_extents is None:
        return _terminal_translation(segments, contract)

    insertion_x = _safe_insertion_x(segments, contract, feature_x_extents)
    if insertion_x is None:
        return _terminal_translation(segments, contract)

    index_by_id = {
        segment.segment_id: index for index, segment in enumerate(segments)
    }
    rail_indices = tuple(
        index_by_id[segment_id] for segment_id in contract.rail_segment_ids
    )
    rail_set = set(rail_indices)
    terminal_indices = {
        index_by_id[segment_id]
        for segment_id in contract.positive_terminal_segment_ids
    }
    terminal_vertices = {
        vertex_index
        for segment_index in terminal_indices
        for vertex_index in (segment_index, (segment_index + 1) % len(segments))
    }
    allowance = float(contract.allowance_mm)
    result: list[Any] = []
    for index, segment in enumerate(segments):
        if index not in rail_set:
            result.append(
                _translated(
                    segment,
                    start_shift=(
                        allowance if index in terminal_vertices else 0.0
                    ),
                    end_shift=(
                        allowance
                        if (index + 1) % len(segments) in terminal_vertices
                        else 0.0
                    ),
                )
            )
            continue

        dx = segment.end[0] - segment.start[0]
        if abs(dx) <= _TOLERANCE_MM:
            return _terminal_translation(segments, contract)
        parameter = (insertion_x - segment.start[0]) / dx
        if parameter <= _TOLERANCE_MM or parameter >= 1.0 - _TOLERANCE_MM:
            return _terminal_translation(segments, contract)
        cut = (
            float(segment.start[0] + parameter * dx),
            float(segment.start[1] + parameter * (segment.end[1] - segment.start[1])),
        )
        shifted_cut = (cut[0] + allowance, cut[1])
        shifted_start = (segment.start[0] + allowance, segment.start[1])
        shifted_end = (segment.end[0] + allowance, segment.end[1])
        bridge_id = f"{segment.segment_id}:allowance-bridge"
        bridge = replace(
            segment,
            segment_id=bridge_id,
            start=(0.0, 0.0),
            end=(0.0, 0.0),
            bulge=0.0,
        )
        if dx < 0.0:
            result.append(replace(segment, start=shifted_start, end=shifted_cut))
            result.append(replace(bridge, start=shifted_cut, end=cut))
            result.append(replace(segment, start=cut, end=segment.end))
        else:
            result.append(replace(segment, start=segment.start, end=cut))
            result.append(replace(bridge, start=cut, end=shifted_cut))
            result.append(replace(segment, start=shifted_cut, end=shifted_end))
    return tuple(result)
