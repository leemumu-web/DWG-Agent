from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from itertools import pairwise
from math import acos, atan2, degrees, dist, hypot, isfinite, tau
from typing import cast

from ezdxf import bbox
from ezdxf.entities import Arc, DXFEntity, Ellipse, Line
from ezdxf.math import ConstructionEllipse, Matrix44
from ezdxf.transform import copies

from .contracts import (
    DevelopedIntervalMetrics,
    DevelopmentMetrics,
    DevelopmentTarget,
    LongitudinalProof,
    PLSplitError,
)
from .geometry import validate_closed_outline
from .longitudinal import canonical_boundary_pieces

K_FACTOR = 0.5
_TENTH_MM = Decimal("0.1")
_DIMENSION_TOLERANCE_MM = 0.001
_NUMERIC_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class _NativePiece:
    source_index: int
    entity: DXFEntity


def _positive_finite(value: float, name: str) -> float:
    resolved = float(value)
    if not isfinite(resolved) or resolved <= 0.0:
        raise PLSplitError("INVALID_LENGTH", f"{name}必须是正的有限毫米值。")
    return resolved


def neutral_axis_length(surface_lengths_mm: tuple[float, float]) -> float:
    first = _positive_finite(surface_lengths_mm[0], "第一板面长度")
    second = _positive_finite(surface_lengths_mm[1], "第二板面长度")
    return (first + second) / 2.0


def ceil_tenth_mm(value_mm: float | Decimal) -> Decimal:
    value = value_mm if isinstance(value_mm, Decimal) else Decimal(str(value_mm))
    if not value.is_finite() or value <= 0:
        raise PLSplitError("INVALID_LENGTH", "展开长度必须是正的有限毫米值。")
    return value.quantize(_TENTH_MM, rounding=ROUND_CEILING)


def calculate_target(
    *,
    projection_length_mm: float,
    k_length_mm: float,
    bom_length_mm: float,
) -> DevelopmentTarget:
    projection = _positive_finite(projection_length_mm, "主视图投影长度")
    k_length = _positive_finite(k_length_mm, "K=0.5中性层长度")
    bom = _positive_finite(bom_length_mm, "材料表长度")
    raw = max(projection, k_length, bom)
    target = float(ceil_tenth_mm(raw))
    return DevelopmentTarget(
        projection_length_mm=projection,
        k_length_mm=k_length,
        bom_length_mm=bom,
        raw_length_mm=raw,
        target_length_mm=target,
        total_extension_mm=target - projection,
    )


def _station_split_error(message_zh: str) -> PLSplitError:
    return PLSplitError("STATION_SPLIT_FAILED", message_zh)


def _entity_bounds(entity: DXFEntity) -> tuple[float, float, float, float]:
    bounds = bbox.extents((entity,), fast=False)
    if not bounds.has_data:
        raise _station_split_error("纵向站位切分实体没有有效范围。")
    return (
        float(bounds.extmin.x),
        float(bounds.extmin.y),
        float(bounds.extmax.x),
        float(bounds.extmax.y),
    )


def _outline_bounds(
    entities: tuple[DXFEntity, ...],
) -> tuple[float, float, float, float]:
    bounds = bbox.extents(entities, fast=False)
    if not bounds.has_data:
        raise PLSplitError("EMPTY_OUTLINE", "主视图外轮廓没有有效范围。")
    return (
        float(bounds.extmin.x),
        float(bounds.extmin.y),
        float(bounds.extmax.x),
        float(bounds.extmax.y),
    )


def _unique_sorted(values: Sequence[float]) -> tuple[float, ...]:
    result: list[float] = []
    for value in sorted(values):
        if not result or abs(value - result[-1]) > _NUMERIC_EPSILON:
            result.append(value)
    return tuple(result)


def _split_line(line: Line, stations: tuple[float, ...]) -> tuple[DXFEntity, ...]:
    start = line.dxf.start
    end = line.dxf.end
    delta = end - start
    if abs(float(delta.x)) <= _NUMERIC_EPSILON:
        return (line.copy(),)
    parameters = _unique_sorted(
        (
            0.0,
            1.0,
            *(
                (station - float(start.x)) / float(delta.x)
                for station in stations
                if _NUMERIC_EPSILON
                < (station - float(start.x)) / float(delta.x)
                < 1.0 - _NUMERIC_EPSILON
            ),
        )
    )
    pieces: list[DXFEntity] = []
    for first, second in pairwise(parameters):
        clone = line.copy()
        clone.dxf.start = start + delta * first
        clone.dxf.end = start + delta * second
        pieces.append(clone)
    return tuple(pieces)


def _curve_split_parameters(
    construction: ConstructionEllipse,
    stations: tuple[float, ...],
) -> tuple[float, ...]:
    span = float(construction.param_span)
    if span <= _NUMERIC_EPSILON:
        raise _station_split_error("纵向站位曲线没有有效逆时针参数范围。")
    major_x = float(construction.major_axis.x)
    minor_x = float(construction.minor_axis.x)
    x_radius = hypot(major_x, minor_x)
    if x_radius <= _NUMERIC_EPSILON:
        return ()
    phase = atan2(minor_x, major_x)
    start = float(construction.start_param)
    relative: list[float] = []
    for station in stations:
        ratio = (station - float(construction.center.x)) / x_radius
        if ratio < -1.0 - _NUMERIC_EPSILON or ratio > 1.0 + _NUMERIC_EPSILON:
            continue
        offset = acos(max(-1.0, min(1.0, ratio)))
        for parameter in (phase - offset, phase + offset):
            distance = (parameter - start) % tau
            if _NUMERIC_EPSILON < distance < span - _NUMERIC_EPSILON:
                relative.append(distance)
    return _unique_sorted(relative)


def _split_arc(arc: Arc, stations: tuple[float, ...]) -> tuple[DXFEntity, ...]:
    construction = ConstructionEllipse.from_arc(
        center=arc.dxf.center,
        radius=float(arc.dxf.radius),
        extrusion=arc.dxf.extrusion,
        start_angle=float(arc.dxf.start_angle),
        end_angle=float(arc.dxf.end_angle),
    )
    relative = _curve_split_parameters(construction, stations)
    if not relative:
        return (arc.copy(),)
    parameters = (0.0, *relative, float(construction.param_span))
    start = float(construction.start_param)
    pieces: list[DXFEntity] = []
    for first, second in pairwise(parameters):
        clone = arc.copy()
        clone.dxf.start_angle = degrees(start + first) % 360.0
        clone.dxf.end_angle = degrees(start + second) % 360.0
        pieces.append(clone)
    return tuple(pieces)


def _split_ellipse(
    ellipse: Ellipse,
    stations: tuple[float, ...],
) -> tuple[DXFEntity, ...]:
    construction = ellipse.construction_tool()
    relative = _curve_split_parameters(construction, stations)
    if not relative:
        return (ellipse.copy(),)
    parameters = (0.0, *relative, float(construction.param_span))
    start = float(construction.start_param)
    pieces: list[DXFEntity] = []
    for first, second in pairwise(parameters):
        clone = ellipse.copy()
        clone.dxf.start_param = (start + first) % tau
        clone.dxf.end_param = (start + second) % tau
        pieces.append(clone)
    return tuple(pieces)


def _split_native_entity(
    entity: DXFEntity,
    stations: tuple[float, ...],
) -> tuple[DXFEntity, ...]:
    entity_type = entity.dxftype()
    if entity_type == "LINE":
        pieces = _split_line(cast(Line, entity), stations)
    elif entity_type == "ARC":
        pieces = _split_arc(cast(Arc, entity), stations)
    elif entity_type == "ELLIPSE":
        pieces = _split_ellipse(cast(Ellipse, entity), stations)
    else:
        raise _station_split_error(f"纵向站位含不支持的原生实体：{entity_type}")
    if not pieces:
        raise _station_split_error("纵向站位没有生成有效原生实体片段。")
    for piece in pieces:
        min_x, _, max_x, _ = _entity_bounds(piece)
        if any(
            min_x + _DIMENSION_TOLERANCE_MM < station < max_x - _DIMENSION_TOLERANCE_MM
            for station in stations
        ):
            raise _station_split_error("原生曲线没有在纵向站位处完整切分。")
    return pieces


def _proof_contract(
    longitudinal: LongitudinalProof,
) -> tuple[int, int, float, float, float, float]:
    intervals = longitudinal.intervals
    carrier = longitudinal.carrier_interval_indices
    if (
        not intervals
        or tuple(interval.index for interval in intervals)
        != tuple(range(len(intervals)))
        or not carrier
        or carrier != tuple(range(carrier[0], carrier[-1] + 1))
        or carrier[0] < 0
        or carrier[-1] >= len(intervals)
    ):
        raise _station_split_error("纵向证明的区间或承载区索引无效。")
    first = intervals[carrier[0]]
    last = intervals[carrier[-1]]
    upper_left = float(first.left_station.upper_x_mm)
    lower_left = float(first.left_station.lower_x_mm)
    upper_span = float(last.right_station.upper_x_mm) - upper_left
    lower_span = float(last.right_station.lower_x_mm) - lower_left
    if (
        not isfinite(upper_span)
        or not isfinite(lower_span)
        or upper_span <= _NUMERIC_EPSILON
        or lower_span <= _NUMERIC_EPSILON
    ):
        raise _station_split_error("纵向承载区上下链必须具有正的有限X跨度。")
    return carrier[0], carrier[-1], upper_left, lower_left, upper_span, lower_span


def _source_station_values(
    longitudinal: LongitudinalProof,
    source_count: int,
) -> tuple[tuple[float, ...], ...]:
    values = [set[float]() for _ in range(source_count)]
    for interval in longitudinal.intervals:
        for source_index in interval.upper_entity_indices:
            if not 0 <= source_index < source_count:
                raise _station_split_error("纵向证明引用了不存在的上边界原生实体。")
            values[source_index].update(
                (
                    float(interval.left_station.upper_x_mm),
                    float(interval.right_station.upper_x_mm),
                )
            )
        for source_index in interval.lower_entity_indices:
            if not 0 <= source_index < source_count:
                raise _station_split_error("纵向证明引用了不存在的下边界原生实体。")
            values[source_index].update(
                (
                    float(interval.left_station.lower_x_mm),
                    float(interval.right_station.lower_x_mm),
                )
            )
    return tuple(_unique_sorted(tuple(source)) for source in values)


def _interval_region(
    index: int, first_carrier: int, last_carrier: int, side: str
) -> str:
    if index < first_carrier:
        return "identity"
    if index > last_carrier:
        return "downstream"
    return f"carrier_{side}"


def _piece_region(
    piece: _NativePiece,
    longitudinal: LongitudinalProof,
    first_carrier: int,
    last_carrier: int,
) -> str:
    min_x, _, max_x, _ = _entity_bounds(piece.entity)
    regions: set[str] = set()
    for interval in longitudinal.intervals:
        for side, indices, left_x, right_x in (
            (
                "upper",
                interval.upper_entity_indices,
                float(interval.left_station.upper_x_mm),
                float(interval.right_station.upper_x_mm),
            ),
            (
                "lower",
                interval.lower_entity_indices,
                float(interval.left_station.lower_x_mm),
                float(interval.right_station.lower_x_mm),
            ),
        ):
            overlap = min(max_x, max(left_x, right_x)) - max(
                min_x,
                min(left_x, right_x),
            )
            if piece.source_index in indices and overlap > _NUMERIC_EPSILON:
                regions.add(
                    _interval_region(interval.index, first_carrier, last_carrier, side)
                )
    if len(regions) == 1:
        return regions.pop()
    if len(regions) > 1:
        raise _station_split_error("纵向站位片段同时属于多个不兼容变换区域。")

    first = longitudinal.intervals[first_carrier]
    last = longitudinal.intervals[last_carrier]
    left_values = (
        float(first.left_station.upper_x_mm),
        float(first.left_station.lower_x_mm),
    )
    right_values = (
        float(last.right_station.upper_x_mm),
        float(last.right_station.lower_x_mm),
    )
    fallback: set[str] = set()
    if max_x <= min(left_values) + _NUMERIC_EPSILON or (
        min_x >= min(left_values) - _NUMERIC_EPSILON
        and max_x <= max(left_values) + _NUMERIC_EPSILON
    ):
        fallback.add("identity")
    if min_x >= max(right_values) - _NUMERIC_EPSILON or (
        min_x >= min(right_values) - _NUMERIC_EPSILON
        and max_x <= max(right_values) + _NUMERIC_EPSILON
    ):
        fallback.add("downstream")
    if len(fallback) != 1:
        raise _station_split_error("纵向站位片段无法唯一归入上游、承载区或下游。")
    return fallback.pop()


def _transform_groups(
    pieces: tuple[_NativePiece, ...],
    longitudinal: LongitudinalProof,
    *,
    first_carrier: int,
    last_carrier: int,
    upper_left: float,
    lower_left: float,
    upper_scale: float,
    lower_scale: float,
    downstream_shift: float,
) -> tuple[_NativePiece, ...]:
    grouped: dict[str, list[_NativePiece]] = {
        "identity": [],
        "carrier_upper": [],
        "carrier_lower": [],
        "downstream": [],
    }
    for piece in pieces:
        grouped[_piece_region(piece, longitudinal, first_carrier, last_carrier)].append(
            piece
        )
    matrices = {
        "identity": Matrix44(),
        "carrier_upper": Matrix44.chain(
            Matrix44.translate(-upper_left, 0.0, 0.0),
            Matrix44.scale(upper_scale, 1.0, 1.0),
            Matrix44.translate(upper_left, 0.0, 0.0),
        ),
        "carrier_lower": Matrix44.chain(
            Matrix44.translate(-lower_left, 0.0, 0.0),
            Matrix44.scale(lower_scale, 1.0, 1.0),
            Matrix44.translate(lower_left, 0.0, 0.0),
        ),
        "downstream": Matrix44.translate(downstream_shift, 0.0, 0.0),
    }
    transformed: list[_NativePiece] = []
    for name in ("identity", "carrier_upper", "carrier_lower", "downstream"):
        source = tuple(grouped[name])
        if not source:
            continue
        log, result = copies(tuple(piece.entity for piece in source), matrices[name])
        if len(log) or len(result) != len(source):
            messages = "; ".join(log.messages())
            raise PLSplitError(
                "TRANSFORM_FAILED",
                f"主视图原生实体无法完整执行分区变换。{messages}",
            )
        transformed.extend(
            _NativePiece(piece.source_index, entity)
            for piece, entity in zip(source, result, strict=True)
        )
    return tuple(transformed)


def _merge_collinear_lines(
    first: DXFEntity,
    second: DXFEntity,
) -> DXFEntity | None:
    if first.dxftype() != "LINE" or second.dxftype() != "LINE":
        return None
    first_points = (first.dxf.start, first.dxf.end)
    second_points = (second.dxf.start, second.dxf.end)
    matches = tuple(
        (first_index, second_index)
        for first_index, first_point in enumerate(first_points)
        for second_index, second_point in enumerate(second_points)
        if dist(
            (float(first_point.x), float(first_point.y)),
            (float(second_point.x), float(second_point.y)),
        )
        <= _DIMENSION_TOLERANCE_MM
    )
    if len(matches) != 1:
        return None
    first_index, second_index = matches[0]
    shared = first_points[first_index]
    first_outer = first_points[1 - first_index]
    second_outer = second_points[1 - second_index]
    first_vector = (
        float(shared.x - first_outer.x),
        float(shared.y - first_outer.y),
    )
    second_vector = (
        float(second_outer.x - shared.x),
        float(second_outer.y - shared.y),
    )
    first_length = hypot(*first_vector)
    second_length = hypot(*second_vector)
    if first_length <= _NUMERIC_EPSILON or second_length <= _NUMERIC_EPSILON:
        return None
    cross = abs(first_vector[0] * second_vector[1] - first_vector[1] * second_vector[0])
    if cross > _DIMENSION_TOLERANCE_MM * min(first_length, second_length):
        return None
    merged = first.copy()
    merged.dxf.start = first_outer
    merged.dxf.end = second_outer
    return merged


def _coalesce_output_lines(
    pieces: tuple[_NativePiece, ...],
) -> tuple[_NativePiece, ...]:
    result = list(pieces)
    while True:
        merged_pair: tuple[int, int, _NativePiece] | None = None
        for first_index, first in enumerate(result):
            for second_index in range(first_index + 1, len(result)):
                second = result[second_index]
                merged = _merge_collinear_lines(first.entity, second.entity)
                if merged is not None:
                    merged_pair = (
                        first_index,
                        second_index,
                        _NativePiece(first.source_index, merged),
                    )
                    break
            if merged_pair is not None:
                break
        if merged_pair is None:
            return tuple(result)
        first_index, second_index, merged = merged_pair
        result[first_index] = merged
        result.pop(second_index)


def _developed_intervals(
    longitudinal: LongitudinalProof,
    *,
    first_carrier: int,
    last_carrier: int,
    upper_left: float,
    lower_left: float,
    upper_scale: float,
    lower_scale: float,
    downstream_shift: float,
) -> tuple[DevelopedIntervalMetrics, ...]:
    result: list[DevelopedIntervalMetrics] = []
    carrier = set(longitudinal.carrier_interval_indices)
    for interval in longitudinal.intervals:
        if interval.index < first_carrier:
            output_upper = interval.upper_span_mm
            output_lower = interval.lower_span_mm
            shift = 0.0
        elif interval.index > last_carrier:
            output_upper = interval.upper_span_mm
            output_lower = interval.lower_span_mm
            shift = downstream_shift
        else:
            upper_start = (
                float(interval.left_station.upper_x_mm) - upper_left
            ) * upper_scale + upper_left
            upper_end = (
                float(interval.right_station.upper_x_mm) - upper_left
            ) * upper_scale + upper_left
            lower_start = (
                float(interval.left_station.lower_x_mm) - lower_left
            ) * lower_scale + lower_left
            lower_end = (
                float(interval.right_station.lower_x_mm) - lower_left
            ) * lower_scale + lower_left
            output_upper = upper_end - upper_start
            output_lower = lower_end - lower_start
            shift = 0.0
        metric = DevelopedIntervalMetrics(
            index=interval.index,
            source_upper_span_mm=interval.upper_span_mm,
            source_lower_span_mm=interval.lower_span_mm,
            output_upper_span_mm=output_upper,
            output_lower_span_mm=output_lower,
            downstream_shift_mm=shift,
            is_carrier=interval.index in carrier,
        )
        if interval.index not in carrier and (
            abs(metric.output_upper_span_mm - metric.source_upper_span_mm)
            > _DIMENSION_TOLERANCE_MM
            or abs(metric.output_lower_span_mm - metric.source_lower_span_mm)
            > _DIMENSION_TOLERANCE_MM
        ):
            raise PLSplitError("TRANSFORM_INTERVAL", "非承载纵向区间的X跨度发生变化。")
        result.append(metric)
    upper_growth = sum(
        metric.output_upper_span_mm - metric.source_upper_span_mm
        for metric in result
        if metric.is_carrier
    )
    lower_growth = sum(
        metric.output_lower_span_mm - metric.source_lower_span_mm
        for metric in result
        if metric.is_carrier
    )
    if (
        abs(upper_growth - downstream_shift) > _DIMENSION_TOLERANCE_MM
        or abs(lower_growth - downstream_shift) > _DIMENSION_TOLERANCE_MM
    ):
        raise PLSplitError("TRANSFORM_INTERVAL", "承载纵向区间没有完整吸收总展开增量。")
    return tuple(result)


def transform_outline(
    entities: Sequence[DXFEntity],
    *,
    longitudinal: LongitudinalProof,
    projection_length_mm: float,
    k_length_mm: float,
    bom_length_mm: float,
    anchor_x_mm: float,
) -> tuple[tuple[DXFEntity, ...], DevelopmentMetrics]:
    source = tuple(entities)
    if not source:
        raise PLSplitError("EMPTY_OUTLINE", "主视图外轮廓不能为空。")
    anchor = float(anchor_x_mm)
    if not isfinite(anchor):
        raise PLSplitError("INVALID_ANCHOR", "主视图左端锚点必须是有限坐标。")
    target = calculate_target(
        projection_length_mm=projection_length_mm,
        k_length_mm=k_length_mm,
        bom_length_mm=bom_length_mm,
    )
    source_min_x, source_min_y, _, source_max_y = _outline_bounds(source)
    source_width = source_max_y - source_min_y
    first, last, upper_left, lower_left, upper_span, lower_span = _proof_contract(
        longitudinal
    )
    upper_scale = (upper_span + target.total_extension_mm) / upper_span
    lower_scale = (lower_span + target.total_extension_mm) / lower_span
    if longitudinal.selection_reason == "uniform_projection_fallback":
        scale = target.target_length_mm / target.projection_length_mm
        matrix = Matrix44.chain(
            Matrix44.translate(-anchor, 0.0, 0.0),
            Matrix44.scale(scale, 1.0, 1.0),
            Matrix44.translate(anchor, 0.0, 0.0),
        )
        log, transformed = copies(
            source,
            matrix,
        )
        if len(log) or len(transformed) != len(source):
            messages = "; ".join(log.messages())
            raise PLSplitError(
                "TRANSFORM_FAILED",
                f"主视图原生实体无法完整执行0.1 mm内等比拉伸。{messages}",
            )
        transformed = tuple(transformed)
    else:
        boundary_pieces = canonical_boundary_pieces(source)
        station_values = _source_station_values(longitudinal, len(source))
        pieces = tuple(
            _NativePiece(boundary_piece.source_index, piece)
            for boundary_piece in boundary_pieces
            for piece in _split_native_entity(
                boundary_piece.entity,
                station_values[boundary_piece.source_index],
            )
        )
        transformed = tuple(
            piece.entity
            for piece in _coalesce_output_lines(
                _transform_groups(
                    pieces,
                    longitudinal,
                    first_carrier=first,
                    last_carrier=last,
                    upper_left=upper_left,
                    lower_left=lower_left,
                    upper_scale=upper_scale,
                    lower_scale=lower_scale,
                    downstream_shift=target.total_extension_mm,
                )
            )
        )
    validate_closed_outline(transformed)
    output_min_x, output_min_y, output_max_x, output_max_y = _outline_bounds(
        transformed
    )
    output_length = output_max_x - output_min_x
    output_width = output_max_y - output_min_y
    if abs(output_length - target.target_length_mm) > _DIMENSION_TOLERANCE_MM:
        raise PLSplitError("TRANSFORM_LENGTH", "分区展开结果长度与目标长度不一致。")
    if abs(output_width - source_width) > _DIMENSION_TOLERANCE_MM:
        raise PLSplitError("TRANSFORM_WIDTH", "分区展开改变了主视图板宽。")
    if abs(output_min_x - anchor) > _DIMENSION_TOLERANCE_MM:
        raise PLSplitError("TRANSFORM_ANCHOR", "分区展开改变了主视图左端锚点。")
    if abs(source_min_x - anchor) > _DIMENSION_TOLERANCE_MM:
        raise PLSplitError("INVALID_ANCHOR", "主视图左端锚点与原生外轮廓不一致。")
    intervals = _developed_intervals(
        longitudinal,
        first_carrier=first,
        last_carrier=last,
        upper_left=upper_left,
        lower_left=lower_left,
        upper_scale=upper_scale,
        lower_scale=lower_scale,
        downstream_shift=target.total_extension_mm,
    )
    return transformed, DevelopmentMetrics(
        projection_length_mm=target.projection_length_mm,
        k_factor=K_FACTOR,
        k_length_mm=target.k_length_mm,
        bom_length_mm=target.bom_length_mm,
        raw_length_mm=target.raw_length_mm,
        target_length_mm=target.target_length_mm,
        total_extension_mm=target.total_extension_mm,
        anchor_x_mm=anchor,
        carrier_interval_indices=longitudinal.carrier_interval_indices,
        carrier_upper_scale_x=upper_scale,
        carrier_lower_scale_x=lower_scale,
        intervals=intervals,
    )
