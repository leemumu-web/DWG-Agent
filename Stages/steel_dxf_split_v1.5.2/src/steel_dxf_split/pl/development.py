from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from itertools import pairwise
from math import acos, atan2, degrees, dist, hypot, isfinite, tau
from typing import cast

from ezdxf import bbox
from ezdxf.entities import Arc, DXFEntity, Ellipse, Line
from ezdxf.math import ConstructionEllipse, Matrix44, Vec3
from ezdxf.transform import copies

from .contracts import (
    DevelopedIntervalMetrics,
    DevelopmentMetrics,
    DevelopmentTarget,
    LongitudinalProof,
    PLSplitError,
)
from .geometry import validate_closed_outline
from .longitudinal import BoundaryPiece, canonical_boundary_pieces

K_FACTOR = 0.5
_TENTH_MM = Decimal("0.1")
_DIMENSION_TOLERANCE_MM = 0.001
_DETAIL_TOLERANCE_MM = 0.1
_NUMERIC_EPSILON = 1e-9
_SHORT_CONNECTOR_MAX_MM = 0.6


@dataclass(frozen=True, slots=True)
class _NativePiece:
    source_index: int
    entity: DXFEntity
    connector_role: str = "ordinary"


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


def _short_connector_roles(
    pieces: tuple[BoundaryPiece, ...],
    *,
    min_y: float,
    max_y: float,
) -> tuple[str, ...]:
    roles = ["ordinary"] * len(pieces)
    middle_y = (min_y + max_y) / 2.0
    candidates: list[tuple[int, str, float, float]] = []
    for index, piece in enumerate(pieces):
        if piece.entity.dxftype() != "LINE":
            continue
        start = Vec3(piece.entity.dxf.start)
        end = Vec3(piece.entity.dxf.end)
        length = start.distance(end)
        if not (_NUMERIC_EPSILON < length <= _SHORT_CONNECTOR_MAX_MM):
            continue
        if abs(end.x - start.x) <= abs(end.y - start.y):
            continue
        connector_y = (start.y + end.y) / 2.0
        side = "upper" if connector_y > middle_y else "lower"
        course_y = max_y if side == "upper" else min_y
        if abs(connector_y - course_y) > _DETAIL_TOLERANCE_MM:
            continue
        candidates.append((index, side, min(start.x, end.x), max(start.x, end.x)))

    for index, side, min_x, max_x in candidates:
        opposite = tuple(
            other_index
            for other_index, other_side, other_min_x, other_max_x in candidates
            if other_index != index
            and other_side != side
            and max(min_x, other_min_x)
            <= min(max_x, other_max_x) + _SHORT_CONNECTOR_MAX_MM
        )
        if len(opposite) > 1:
            continue
        if side == "lower" and len(opposite) == 1:
            roles[index] = "preserve_lower"
        elif side == "upper":
            roles[index] = "collapse"
    return tuple(roles)


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
            _NativePiece(piece.source_index, entity, piece.connector_role)
            for piece, entity in zip(source, result, strict=True)
        )
    return tuple(transformed)


def _oriented_line_points(entity: DXFEntity) -> tuple[Vec3, Vec3]:
    line = cast(Line, entity)
    start = Vec3(line.dxf.start)
    end = Vec3(line.dxf.end)
    return (start, end) if start.y <= end.y else (end, start)


def _move_line_node(
    pieces: tuple[_NativePiece, ...],
    old: Vec3,
    new_x: float,
) -> tuple[_NativePiece, ...]:
    moved: list[_NativePiece] = []
    matched = 0
    for piece in pieces:
        if piece.entity.dxftype() != "LINE":
            moved.append(piece)
            continue
        entity = piece.entity.copy()
        for attribute in ("start", "end"):
            point = Vec3(getattr(entity.dxf, attribute))
            if point.distance(old) <= _DIMENSION_TOLERANCE_MM:
                setattr(entity.dxf, attribute, (new_x, old.y, old.z))
                matched += 1
        moved.append(_NativePiece(piece.source_index, entity, piece.connector_role))
    if matched != 2:
        raise _station_split_error("端边斜率校正节点没有唯一连接两条原生 LINE。")
    return tuple(moved)


def _normalize_terminal_pair(
    pieces: tuple[_NativePiece, ...],
    longitudinal: LongitudinalProof,
) -> tuple[tuple[_NativePiece, ...], float, float]:
    if longitudinal.carrier_interval_indices[-1] != len(longitudinal.intervals) - 1:
        return pieces, 0.0, 0.0
    entities = tuple(piece.entity for piece in pieces)
    _, min_y, _, max_y = _outline_bounds(entities)
    height = max_y - min_y
    candidates = tuple(
        piece
        for piece in pieces
        if piece.entity.dxftype() == "LINE"
        and abs(abs(float(piece.entity.dxf.end.y - piece.entity.dxf.start.y)) - height)
        <= _DETAIL_TOLERANCE_MM
    )
    if len(candidates) != 2:
        return pieces, 0.0, 0.0
    left, right = sorted(
        candidates,
        key=lambda piece: sum(point.x for point in _oriented_line_points(piece.entity)),
    )
    left_lower, left_upper = _oriented_line_points(left.entity)
    right_lower, right_upper = _oriented_line_points(right.entity)
    left_dy = left_upper.y - left_lower.y
    right_dy = right_upper.y - right_lower.y
    if (
        left_dy <= _NUMERIC_EPSILON
        or right_dy <= _NUMERIC_EPSILON
        or abs(left_dy - right_dy) > _DETAIL_TOLERANCE_MM
    ):
        return pieces, 0.0, 0.0
    desired_right_dx = (left_upper.x - left_lower.x) * right_dy / left_dy
    actual_right_dx = right_upper.x - right_lower.x
    difference = actual_right_dx - desired_right_dx
    if abs(difference) <= _NUMERIC_EPSILON or abs(difference) > _DETAIL_TOLERANCE_MM:
        return pieces, 0.0, 0.0
    if right_upper.x >= right_lower.x:
        new_lower_x = right_upper.x - desired_right_dx
        return (
            _move_line_node(pieces, right_lower, new_lower_x),
            0.0,
            new_lower_x - right_lower.x,
        )
    new_upper_x = right_lower.x + desired_right_dx
    return (
        _move_line_node(pieces, right_upper, new_upper_x),
        new_upper_x - right_upper.x,
        0.0,
    )


def _normalize_short_connectors(
    pieces: tuple[_NativePiece, ...],
) -> tuple[tuple[_NativePiece, ...], tuple[Vec3, ...]]:
    result = list(pieces)
    protected = [
        point
        for piece in result
        if piece.connector_role == "preserve_lower"
        for point in (Vec3(piece.entity.dxf.start), Vec3(piece.entity.dxf.end))
    ]
    collapse = tuple(piece for piece in result if piece.connector_role == "collapse")
    for connector in collapse:
        try:
            connector_index = result.index(connector)
        except ValueError:
            continue
        if connector.entity.dxftype() != "LINE":
            raise PLSplitError("TRANSFORM_CONNECTOR", "制造短连接不是原生 LINE。")
        connector_points = (
            Vec3(connector.entity.dxf.start),
            Vec3(connector.entity.dxf.end),
        )
        midpoint = (connector_points[0] + connector_points[1]) * 0.5
        neighbours: list[tuple[int, str]] = []
        for connector_point in connector_points:
            matches: list[tuple[int, str]] = []
            for piece_index, piece in enumerate(result):
                if piece_index == connector_index or piece.entity.dxftype() != "LINE":
                    continue
                for attribute in ("start", "end"):
                    point = Vec3(getattr(piece.entity.dxf, attribute))
                    if point.distance(connector_point) <= _DETAIL_TOLERANCE_MM:
                        matches.append((piece_index, attribute))
            if len(matches) != 1:
                raise PLSplitError(
                    "TRANSFORM_CONNECTOR",
                    "制造短连接无法唯一压缩为闭合轮廓节点。",
                )
            neighbours.append(matches[0])
        if neighbours[0][0] == neighbours[1][0]:
            raise PLSplitError(
                "TRANSFORM_CONNECTOR",
                "制造短连接两端不能连接同一条原生 LINE。",
            )
        for piece_index, attribute in neighbours:
            piece = result[piece_index]
            entity = piece.entity.copy()
            setattr(entity.dxf, attribute, midpoint)
            result[piece_index] = _NativePiece(
                piece.source_index,
                entity,
                piece.connector_role,
            )
        result.pop(connector_index)
        protected.append(midpoint)
    return tuple(result), tuple(protected)


def _merge_collinear_lines(
    first: DXFEntity,
    second: DXFEntity,
    *,
    protected_nodes: tuple[Vec3, ...] = (),
    allow_detail_deviation: bool = False,
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
    if any(
        Vec3(shared).distance(protected) <= _DETAIL_TOLERANCE_MM
        for protected in protected_nodes
    ):
        return None
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
        if not allow_detail_deviation:
            return None
        chord = (
            float(second_outer.x - first_outer.x),
            float(second_outer.y - first_outer.y),
        )
        chord_length = hypot(*chord)
        if chord_length <= _NUMERIC_EPSILON:
            return None
        deviation = (
            abs(first_vector[0] * chord[1] - first_vector[1] * chord[0]) / chord_length
        )
        if deviation > _DETAIL_TOLERANCE_MM:
            return None
    if first_vector[0] * second_vector[0] + first_vector[1] * second_vector[1] <= 0.0:
        return None
    merged = first.copy()
    merged.dxf.start = first_outer
    merged.dxf.end = second_outer
    return merged


def _coalesce_output_lines(
    pieces: tuple[_NativePiece, ...],
    *,
    protected_nodes: tuple[Vec3, ...] = (),
) -> tuple[_NativePiece, ...]:
    result = list(pieces)
    min_x, _, max_x, _ = _outline_bounds(tuple(piece.entity for piece in pieces))
    while True:
        merged_pair: tuple[int, int, _NativePiece] | None = None
        for first_index, first in enumerate(result):
            for second_index in range(first_index + 1, len(result)):
                second = result[second_index]
                endpoints = (
                    (
                        Vec3(first.entity.dxf.start),
                        Vec3(first.entity.dxf.end),
                        Vec3(second.entity.dxf.start),
                        Vec3(second.entity.dxf.end),
                    )
                    if first.entity.dxftype() == second.entity.dxftype() == "LINE"
                    else ()
                )
                touches_terminal = any(
                    abs(point.x - min_x) <= _DETAIL_TOLERANCE_MM
                    or abs(point.x - max_x) <= _DETAIL_TOLERANCE_MM
                    for point in endpoints
                )
                merged = _merge_collinear_lines(
                    first.entity,
                    second.entity,
                    protected_nodes=protected_nodes,
                )
                relaxed_terminal_merge = False
                if (
                    merged is None
                    and touches_terminal
                    and first.source_index != second.source_index
                    and first.connector_role != "terminal_coalesced"
                    and second.connector_role != "terminal_coalesced"
                ):
                    merged = _merge_collinear_lines(
                        first.entity,
                        second.entity,
                        protected_nodes=protected_nodes,
                        allow_detail_deviation=True,
                    )
                    relaxed_terminal_merge = merged is not None
                if merged is not None:
                    merged_pair = (
                        first_index,
                        second_index,
                        _NativePiece(
                            first.source_index,
                            merged,
                            (
                                "terminal_coalesced"
                                if relaxed_terminal_merge
                                or first.connector_role == "terminal_coalesced"
                                or second.connector_role == "terminal_coalesced"
                                else first.connector_role
                            ),
                        ),
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
    upper_terminal_correction: float,
    lower_terminal_correction: float,
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
            if interval.index == last_carrier:
                output_upper += upper_terminal_correction
                output_lower += lower_terminal_correction
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
    growth_errors = (
        abs(upper_growth - downstream_shift),
        abs(lower_growth - downstream_shift),
    )
    if (
        max(growth_errors) > _DETAIL_TOLERANCE_MM
        or min(growth_errors) > _DIMENSION_TOLERANCE_MM
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
    k_factor: float | None = K_FACTOR,
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
        try:
            boundary_pieces = canonical_boundary_pieces(source)
            connector_roles = _short_connector_roles(
                boundary_pieces,
                min_y=source_min_y,
                max_y=source_max_y,
            )
            native_boundary = tuple(
                _NativePiece(piece.source_index, piece.entity, role)
                for piece, role in zip(
                    boundary_pieces,
                    connector_roles,
                    strict=True,
                )
            )
        except PLSplitError as error:
            if error.code != "LONGITUDINAL_TOPOLOGY":
                raise
            native_boundary = tuple(
                _NativePiece(source_index, entity)
                for source_index, entity in enumerate(source)
            )
        scale = target.target_length_mm / target.projection_length_mm
        matrix = Matrix44.chain(
            Matrix44.translate(-anchor, 0.0, 0.0),
            Matrix44.scale(scale, 1.0, 1.0),
            Matrix44.translate(anchor, 0.0, 0.0),
        )
        log, transformed = copies(
            tuple(piece.entity for piece in native_boundary),
            matrix,
        )
        if len(log) or len(transformed) != len(native_boundary):
            messages = "; ".join(log.messages())
            raise PLSplitError(
                "TRANSFORM_FAILED",
                f"主视图原生实体无法完整执行0.1 mm内等比拉伸。{messages}",
            )
        uniform_pieces = tuple(
            _NativePiece(piece.source_index, entity, piece.connector_role)
            for piece, entity in zip(native_boundary, transformed, strict=True)
        )
        uniform_pieces, protected_nodes = _normalize_short_connectors(uniform_pieces)
        transformed = tuple(
            piece.entity
            for piece in _coalesce_output_lines(
                uniform_pieces,
                protected_nodes=protected_nodes,
            )
        )
        upper_terminal_correction = 0.0
        lower_terminal_correction = 0.0
    else:
        boundary_pieces = canonical_boundary_pieces(source)
        connector_roles = _short_connector_roles(
            boundary_pieces,
            min_y=source_min_y,
            max_y=source_max_y,
        )
        station_values = _source_station_values(longitudinal, len(source))
        pieces = tuple(
            _NativePiece(boundary_piece.source_index, piece, connector_role)
            for boundary_piece, connector_role in zip(
                boundary_pieces,
                connector_roles,
                strict=True,
            )
            for piece in _split_native_entity(
                boundary_piece.entity,
                station_values[boundary_piece.source_index],
            )
        )
        grouped = _transform_groups(
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
        grouped, upper_terminal_correction, lower_terminal_correction = (
            _normalize_terminal_pair(grouped, longitudinal)
        )
        grouped, protected_nodes = _normalize_short_connectors(grouped)
        transformed = tuple(
            piece.entity
            for piece in _coalesce_output_lines(
                grouped,
                protected_nodes=protected_nodes,
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
        upper_terminal_correction=upper_terminal_correction,
        lower_terminal_correction=lower_terminal_correction,
    )
    return transformed, DevelopmentMetrics(
        projection_length_mm=target.projection_length_mm,
        k_factor=k_factor,
        k_length_mm=target.k_length_mm,
        bom_length_mm=target.bom_length_mm,
        raw_length_mm=target.raw_length_mm,
        target_length_mm=target.target_length_mm,
        total_extension_mm=target.total_extension_mm,
        anchor_x_mm=anchor,
        carrier_interval_indices=longitudinal.carrier_interval_indices,
        carrier_upper_scale_x=(
            upper_span + target.total_extension_mm + upper_terminal_correction
        )
        / upper_span,
        carrier_lower_scale_x=(
            lower_span + target.total_extension_mm + lower_terminal_correction
        )
        / lower_span,
        intervals=intervals,
    )
