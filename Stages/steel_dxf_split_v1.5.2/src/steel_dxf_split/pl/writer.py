from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import acos, atan2, dist, hypot, tau
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.entities import DXFEntity
from ezdxf.enums import TextEntityAlignment
from ezdxf.math import ConstructionEllipse
from shapely.geometry import LineString
from shapely.ops import polylabel, unary_union

from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.part_mark_layout import (
    PartMarkLayoutError,
    PartMarkTarget,
    layout_part_marks,
    part_mark_envelope,
)

from .contracts import DevelopedPlate, PLSplitError, PLWriteResult
from .geometry import flatten_entity, validate_closed_outline

_WINDOWS_CJK_DXF_FONT = "simsun.ttc"
_DIMENSION_TOLERANCE_MM = 0.001
PL_LABEL_HEIGHT_MM = 30.0
PL_LABEL_HEIGHT_OPTIONS_MM = (30.0, 25.0, 20.0, 15.0, 10.0)


def _ensure_layers(document: ezdxf.document.Drawing) -> None:
    for name, color in {
        "PLATE_CUT": 7,
        "PART_LABEL": 3,
        "SPLIT_NOTE": 5,
    }.items():
        if name not in document.layers:
            document.layers.add(name, color=color)


def _ensure_style(document: ezdxf.document.Drawing) -> str:
    if "SplitChinese" not in document.styles:
        document.styles.add("SplitChinese", font=_WINDOWS_CJK_DXF_FONT)
    return "SplitChinese"


def _manufacturing_clone(entity: DXFEntity) -> DXFEntity:
    clone = entity.copy()
    clone.dxf.layer = "PLATE_CUT"
    for attribute in ("color", "linetype", "lineweight", "transparency"):
        clone.dxf.discard(attribute)
    return clone


def _dimension_error(code: str, message: str) -> PLSplitError:
    return PLSplitError(code, message)


def _interval_error(message: str) -> PLSplitError:
    return PLSplitError("OUTPUT_INTERVAL_CONTRACT", message)


@dataclass(frozen=True, slots=True)
class _BoundaryTopology:
    coordinates: tuple[tuple[float, float], ...]
    cycle: tuple[int, ...]


def _boundary_topology(
    entities: tuple[DXFEntity, ...],
    required_points: tuple[tuple[float, float], ...] = (),
) -> _BoundaryTopology:
    endpoints: list[tuple[float, float]] = []
    raw_edges: list[tuple[int, int]] = []
    for entity in entities:
        points = flatten_entity(entity)
        split_points: list[tuple[float, tuple[float, float]]] = []
        if entity.dxftype() == "LINE":
            start_point = points[0]
            end_point = points[-1]
            delta_x = end_point[0] - start_point[0]
            delta_y = end_point[1] - start_point[1]
            length_squared = delta_x * delta_x + delta_y * delta_y
            if length_squared > 0.0:
                for point in required_points:
                    parameter = (
                        (point[0] - start_point[0]) * delta_x
                        + (point[1] - start_point[1]) * delta_y
                    ) / length_squared
                    if not 0.0 < parameter < 1.0:
                        continue
                    projected = (
                        start_point[0] + parameter * delta_x,
                        start_point[1] + parameter * delta_y,
                    )
                    if dist(projected, point) <= _DIMENSION_TOLERANCE_MM:
                        split_points.append((parameter, point))
        unique_split_points: list[tuple[float, tuple[float, float]]] = []
        for parameter, point in sorted(split_points):
            if (
                dist(point, points[0]) <= _DIMENSION_TOLERANCE_MM
                or dist(point, points[-1]) <= _DIMENSION_TOLERANCE_MM
                or (
                    unique_split_points
                    and dist(point, unique_split_points[-1][1])
                    <= _DIMENSION_TOLERANCE_MM
                )
            ):
                continue
            unique_split_points.append((parameter, point))
        entity_points = (
            points[0],
            *(point for _, point in unique_split_points),
            points[-1],
        )
        start = len(endpoints)
        endpoints.extend(entity_points)
        raw_edges.extend(
            (start + index, start + index + 1)
            for index in range(len(entity_points) - 1)
        )
    parents = list(range(len(endpoints)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def join(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parents[second_root] = first_root

    for index, point in enumerate(endpoints):
        for previous in range(index):
            if dist(point, endpoints[previous]) <= _DIMENSION_TOLERANCE_MM:
                join(index, previous)
    members: dict[int, list[tuple[float, float]]] = {}
    for index, point in enumerate(endpoints):
        members.setdefault(find(index), []).append(point)
    root_to_node = {root: index for index, root in enumerate(sorted(members))}
    coordinates = tuple(
        (
            sum(point[0] for point in members[root]) / len(members[root]),
            sum(point[1] for point in members[root]) / len(members[root]),
        )
        for root in sorted(members)
    )
    edges = tuple(
        (root_to_node[find(first)], root_to_node[find(second)])
        for first, second in raw_edges
    )
    if any(first == second for first, second in edges):
        raise _interval_error("结果外轮廓含退化的原生边界实体。")
    adjacency: dict[int, list[int]] = {index: [] for index in range(len(coordinates))}
    for edge_index, (first, second) in enumerate(edges):
        adjacency[first].append(edge_index)
        adjacency[second].append(edge_index)
    if not coordinates or any(
        len(edge_indices) != 2 for edge_indices in adjacency.values()
    ):
        raise _interval_error("结果外轮廓不能重建为唯一闭合边界链。")
    start = min(range(len(coordinates)), key=lambda index: (*coordinates[index], index))
    cycle: list[int] = []
    visited_edges: set[int] = set()
    current = start
    previous_edge: int | None = None
    while True:
        cycle.append(current)
        candidates = tuple(
            edge_index
            for edge_index in adjacency[current]
            if edge_index != previous_edge and edge_index not in visited_edges
        )
        if not candidates:
            raise _interval_error("结果外轮廓边界链在闭合前中断。")
        if previous_edge is None:
            edge_index = min(
                candidates,
                key=lambda candidate: coordinates[
                    edges[candidate][1]
                    if edges[candidate][0] == current
                    else edges[candidate][0]
                ],
            )
        elif len(candidates) == 1:
            edge_index = candidates[0]
        else:
            raise _interval_error("结果外轮廓边界链存在分叉。")
        visited_edges.add(edge_index)
        first, second = edges[edge_index]
        next_node = second if first == current else first
        previous_edge = edge_index
        if next_node == start:
            break
        if next_node in cycle:
            raise _interval_error("结果外轮廓含未闭合的独立边界环。")
        current = next_node
    if len(visited_edges) != len(edges) or len(cycle) != len(coordinates):
        raise _interval_error("结果外轮廓不能重建为单一制造边界环。")
    return _BoundaryTopology(coordinates, tuple(cycle))


def _source_entity_y_values(
    entity: DXFEntity,
    station_x: float,
) -> tuple[float, ...]:
    if entity.dxftype() == "LINE":
        start = entity.dxf.start
        end = entity.dxf.end
        delta_x = float(end.x - start.x)
        if abs(delta_x) <= 1e-12:
            if abs(float(start.x) - station_x) <= _DIMENSION_TOLERANCE_MM:
                return (float(start.y), float(end.y))
            return ()
        parameter = (station_x - float(start.x)) / delta_x
        if -1e-9 <= parameter <= 1.0 + 1e-9:
            return (float(start.y + (end.y - start.y) * parameter),)
        return ()
    if entity.dxftype() == "ARC":
        construction = ConstructionEllipse.from_arc(
            center=entity.dxf.center,
            radius=float(entity.dxf.radius),
            extrusion=entity.dxf.extrusion,
            start_angle=float(entity.dxf.start_angle),
            end_angle=float(entity.dxf.end_angle),
        )
    elif entity.dxftype() == "ELLIPSE":
        construction = entity.construction_tool()
    else:
        return ()
    major_x = float(construction.major_axis.x)
    minor_x = float(construction.minor_axis.x)
    x_radius = hypot(major_x, minor_x)
    if x_radius <= 1e-12:
        return ()
    ratio = (station_x - float(construction.center.x)) / x_radius
    if ratio < -1.0 - 1e-9 or ratio > 1.0 + 1e-9:
        return ()
    phase = atan2(minor_x, major_x)
    offset = acos(max(-1.0, min(1.0, ratio)))
    start_param = float(construction.start_param)
    span = float(construction.param_span)
    parameters = tuple(
        parameter
        for parameter in (phase - offset, phase + offset)
        if (parameter - start_param) % tau <= span + 1e-9
    )
    return tuple(float(point.y) for point in construction.vertices(parameters))


def _source_station_y(
    developed: DevelopedPlate,
    station_index: int,
    station_x: float,
    *,
    upper: bool,
) -> float:
    source_entities = developed.outline.outer_entities
    intervals = developed.longitudinal.intervals
    adjacent = (
        (intervals[0],)
        if station_index == 0
        else (intervals[-1],)
        if station_index == len(intervals)
        else (intervals[station_index - 1], intervals[station_index])
    )
    source_indices = {
        source_index
        for interval in adjacent
        for source_index in (
            interval.upper_entity_indices if upper else interval.lower_entity_indices
        )
    }
    values = tuple(
        value
        for source_index in source_indices
        for value in _source_entity_y_values(
            source_entities[source_index],
            station_x,
        )
    )
    if not values:
        raise _interval_error("源纵向证明的上下链站位无法在原生边界上定位。")
    return max(values) if upper else min(values)


def _source_station_is_line_interior(
    developed: DevelopedPlate,
    station_index: int,
    station_x: float,
    *,
    upper: bool,
) -> bool:
    intervals = developed.longitudinal.intervals
    adjacent = (
        (intervals[0],)
        if station_index == 0
        else (intervals[-1],)
        if station_index == len(intervals)
        else (intervals[station_index - 1], intervals[station_index])
    )
    source_indices = {
        source_index
        for interval in adjacent
        for source_index in (
            interval.upper_entity_indices if upper else interval.lower_entity_indices
        )
    }
    target_y = _source_station_y(
        developed,
        station_index,
        station_x,
        upper=upper,
    )
    for source_index in source_indices:
        entity = developed.outline.outer_entities[source_index]
        if entity.dxftype() != "LINE":
            continue
        start = entity.dxf.start
        end = entity.dxf.end
        delta_x = float(end.x - start.x)
        if abs(delta_x) <= 1e-12:
            continue
        parameter = (station_x - float(start.x)) / delta_x
        if not 1e-9 < parameter < 1.0 - 1e-9:
            continue
        y = float(start.y + (end.y - start.y) * parameter)
        if abs(y - target_y) <= _DIMENSION_TOLERANCE_MM:
            return True
    return False


def _station_nodes(
    topology: _BoundaryTopology,
    expected_points: tuple[tuple[float, float], ...],
) -> tuple[int, ...]:
    result: list[int] = []
    for expected in expected_points:
        matches = tuple(
            index
            for index, point in enumerate(topology.coordinates)
            if dist(point, expected) <= _DIMENSION_TOLERANCE_MM
        )
        if len(matches) != 1:
            raise _interval_error("结果边界链没有唯一保留源证明指定的展开站位。")
        result.append(matches[0])
    if len(set(result)) != len(result):
        raise _interval_error("结果边界链的多个展开站位错误地落在同一拓扑节点。")
    return tuple(result)


def _cycle_path(
    cycle: tuple[int, ...],
    start: int,
    end: int,
    step: int,
) -> tuple[int, ...]:
    index = cycle.index(start)
    result = [start]
    while result[-1] != end:
        index = (index + step) % len(cycle)
        if cycle[index] == start:
            raise _interval_error("结果上下边界链不能连接全部源证明站位。")
        result.append(cycle[index])
    return tuple(result)


def _designated_chain_points(
    topology: _BoundaryTopology,
    station_nodes: tuple[int, ...],
    other_station_nodes: tuple[int, ...],
) -> tuple[tuple[float, float], ...]:
    foreign = set(other_station_nodes) - set(station_nodes)
    candidates: list[tuple[int, ...]] = []
    for step in (1, -1):
        path = _cycle_path(topology.cycle, station_nodes[0], station_nodes[-1], step)
        positions = tuple(path.index(node) for node in station_nodes if node in path)
        if (
            len(positions) == len(station_nodes)
            and positions == tuple(sorted(positions))
            and not foreign.intersection(path)
        ):
            candidates.append(path)
    if len(candidates) != 1:
        raise _interval_error("结果外轮廓不能唯一重建源证明指定的上下边界链。")
    return tuple(topology.coordinates[node] for node in station_nodes)


def _expected_station_points(
    developed: DevelopedPlate,
    source_upper_x: tuple[float, ...],
    source_lower_x: tuple[float, ...],
    output_upper_x: tuple[float, ...],
    output_lower_x: tuple[float, ...],
) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]]:
    upper = tuple(
        (
            output_x,
            _source_station_y(developed, index, source_x, upper=True),
        )
        for index, (source_x, output_x) in enumerate(
            zip(source_upper_x, output_upper_x, strict=True)
        )
    )
    lower = tuple(
        (
            output_x,
            _source_station_y(developed, index, source_x, upper=False),
        )
        for index, (source_x, output_x) in enumerate(
            zip(source_lower_x, output_lower_x, strict=True)
        )
    )
    return upper, lower


def _validate_saved_intervals(
    plate_entities: tuple[DXFEntity, ...],
    developed: DevelopedPlate,
) -> None:
    proof_intervals = developed.longitudinal.intervals
    metrics = developed.metrics
    interval_metrics = metrics.intervals
    expected_indices = tuple(range(len(proof_intervals)))
    carrier = metrics.carrier_interval_indices
    if (
        not proof_intervals
        or tuple(interval.index for interval in proof_intervals) != expected_indices
        or tuple(interval.index for interval in interval_metrics) != expected_indices
        or carrier != developed.longitudinal.carrier_interval_indices
        or not carrier
        or carrier != tuple(range(carrier[0], carrier[-1] + 1))
        or carrier[-1] >= len(proof_intervals)
        or tuple(interval.index for interval in interval_metrics if interval.is_carrier)
        != carrier
    ):
        raise _interval_error("源证明没有提供唯一且连续的承载区间。")
    for proof, metric in zip(proof_intervals, interval_metrics, strict=True):
        if (
            abs(metric.source_upper_span_mm - proof.upper_span_mm)
            > _DIMENSION_TOLERANCE_MM
            or abs(metric.source_lower_span_mm - proof.lower_span_mm)
            > _DIMENSION_TOLERANCE_MM
        ):
            raise _interval_error("展开区间与源纵向证明不一致。")
        if not metric.is_carrier and (
            abs(metric.output_upper_span_mm - metric.source_upper_span_mm)
            > _DIMENSION_TOLERANCE_MM
            or abs(metric.output_lower_span_mm - metric.source_lower_span_mm)
            > _DIMENSION_TOLERANCE_MM
        ):
            raise _interval_error("结果非承载区间的跨度发生变化。")
        expected_shift = (
            metrics.total_extension_mm if metric.index > carrier[-1] else 0.0
        )
        if abs(metric.downstream_shift_mm - expected_shift) > _DIMENSION_TOLERANCE_MM:
            raise _interval_error("结果区间的下游位移与总展开增量不一致。")
    upper_growth = sum(
        interval.output_upper_span_mm - interval.source_upper_span_mm
        for interval in interval_metrics
        if interval.is_carrier
    )
    lower_growth = sum(
        interval.output_lower_span_mm - interval.source_lower_span_mm
        for interval in interval_metrics
        if interval.is_carrier
    )
    if (
        abs(upper_growth - metrics.total_extension_mm) > _DIMENSION_TOLERANCE_MM
        or abs(lower_growth - metrics.total_extension_mm) > _DIMENSION_TOLERANCE_MM
    ):
        raise _interval_error("结果承载区间没有完整吸收总展开增量。")
    source_upper = (
        float(proof_intervals[0].left_station.upper_x_mm),
        *(float(interval.right_station.upper_x_mm) for interval in proof_intervals),
    )
    source_lower = (
        float(proof_intervals[0].left_station.lower_x_mm),
        *(float(interval.right_station.lower_x_mm) for interval in proof_intervals),
    )
    expected_upper = [source_upper[0]]
    expected_lower = [source_lower[0]]
    for interval in interval_metrics:
        expected_upper.append(expected_upper[-1] + interval.output_upper_span_mm)
        expected_lower.append(expected_lower[-1] + interval.output_lower_span_mm)
    expected_upper_points, expected_lower_points = _expected_station_points(
        developed,
        source_upper,
        source_lower,
        tuple(expected_upper),
        tuple(expected_lower),
    )
    virtual_station_points = tuple(
        point
        for upper, source_values, expected_points in (
            (True, source_upper, expected_upper_points),
            (False, source_lower, expected_lower_points),
        )
        for index, (source_x, point) in enumerate(
            zip(source_values, expected_points, strict=True)
        )
        if _source_station_is_line_interior(
            developed,
            index,
            source_x,
            upper=upper,
        )
    )
    topology = _boundary_topology(plate_entities, virtual_station_points)
    upper_nodes = _station_nodes(topology, expected_upper_points)
    lower_nodes = _station_nodes(topology, expected_lower_points)
    measured_upper = _designated_chain_points(topology, upper_nodes, lower_nodes)
    measured_lower = _designated_chain_points(topology, lower_nodes, upper_nodes)
    for index, interval in enumerate(interval_metrics):
        measured_upper_span = measured_upper[index + 1][0] - measured_upper[index][0]
        measured_lower_span = measured_lower[index + 1][0] - measured_lower[index][0]
        if (
            abs(measured_upper_span - interval.output_upper_span_mm)
            > _DIMENSION_TOLERANCE_MM
            or abs(measured_lower_span - interval.output_lower_span_mm)
            > _DIMENSION_TOLERANCE_MM
        ):
            raise _interval_error("结果外轮廓的展开区间跨度与审计指标不一致。")
    expected_upper_shift = 0.0
    expected_lower_shift = 0.0
    for index, interval in enumerate(interval_metrics):
        if (
            abs(measured_upper[index][0] - source_upper[index] - expected_upper_shift)
            > _DIMENSION_TOLERANCE_MM
            or abs(
                measured_lower[index][0] - source_lower[index] - expected_lower_shift
            )
            > _DIMENSION_TOLERANCE_MM
        ):
            raise _interval_error("结果上下边界链的绝对下游位移不一致。")
        expected_upper_shift += (
            interval.output_upper_span_mm - interval.source_upper_span_mm
        )
        expected_lower_shift += (
            interval.output_lower_span_mm - interval.source_lower_span_mm
        )
    if (
        abs(measured_upper[-1][0] - source_upper[-1] - expected_upper_shift)
        > _DIMENSION_TOLERANCE_MM
        or abs(measured_lower[-1][0] - source_lower[-1] - expected_lower_shift)
        > _DIMENSION_TOLERANCE_MM
    ):
        raise _interval_error("结果上下边界链的末端下游位移不一致。")
    carrier_first = carrier[0]
    carrier_end = carrier[-1] + 1
    actual_upper_growth = (
        measured_upper[carrier_end][0]
        - measured_upper[carrier_first][0]
        - (source_upper[carrier_end] - source_upper[carrier_first])
    )
    actual_lower_growth = (
        measured_lower[carrier_end][0]
        - measured_lower[carrier_first][0]
        - (source_lower[carrier_end] - source_lower[carrier_first])
    )
    if (
        abs(actual_upper_growth - metrics.total_extension_mm) > _DIMENSION_TOLERANCE_MM
        or abs(actual_lower_growth - metrics.total_extension_mm)
        > _DIMENSION_TOLERANCE_MM
    ):
        raise _interval_error("结果上下承载链没有共同吸收总展开增量。")


def validate_saved_pl_dxf(
    output_path: str | Path,
    developed: DevelopedPlate,
) -> PLWriteResult:
    target = Path(output_path).resolve()
    try:
        document = load_document(target)
    except Exception as error:
        raise PLSplitError(
            "OUTPUT_LOAD_FAILED", f"结果 DXF 无法重新审计读取：{error}"
        ) from error
    if document.dxfversion != "AC1021":
        raise PLSplitError("OUTPUT_DXF_VERSION", "PL 结果必须是 R2007 DXF。")
    if int(document.header.get("$INSUNITS", 0)) != 4:
        raise PLSplitError("OUTPUT_UNITS", "PL 结果单位必须是毫米。")
    modelspace = tuple(document.modelspace())
    plate_entities = tuple(
        entity for entity in modelspace if entity.dxf.layer == "PLATE_CUT"
    )
    label_entities = tuple(
        entity for entity in modelspace if entity.dxf.layer == "PART_LABEL"
    )
    if len(modelspace) != len(plate_entities) + len(label_entities):
        raise PLSplitError(
            "OUTPUT_ENTITY_CONTRACT",
            "结果 DXF 含 PLATE_CUT 和 PART_LABEL 之外的模型空间实体。",
        )
    if not plate_entities or any(
        entity.dxftype() not in {"LINE", "ARC", "ELLIPSE"} for entity in plate_entities
    ):
        raise PLSplitError(
            "OUTPUT_ENTITY_CONTRACT",
            "结果 PLATE_CUT 含不支持的原生实体类型。",
        )
    expected_cutout_polygons = tuple(
        validate_closed_outline(group)
        for group in developed.transformed_cutout_entity_groups
    )
    cutout_zones = tuple(
        polygon.boundary.buffer(0.1, cap_style="flat", join_style="mitre")
        for polygon in expected_cutout_polygons
    )
    outer_entities_list = []
    saved_cutout_groups: list[list[DXFEntity]] = [[] for _ in expected_cutout_polygons]
    for entity in plate_entities:
        line = LineString(flatten_entity(entity))
        matches = tuple(
            index for index, zone in enumerate(cutout_zones) if zone.covers(line)
        )
        if len(matches) > 1:
            raise PLSplitError("OUTPUT_ENTITY_CONTRACT", "结果PL孔槽边界归属不唯一。")
        if matches:
            saved_cutout_groups[matches[0]].append(entity)
        else:
            outer_entities_list.append(entity)
    outer_entities = tuple(outer_entities_list)
    cutout_polygons = tuple(
        validate_closed_outline(tuple(group)) for group in saved_cutout_groups
    )
    if any(
        saved.symmetric_difference(expected).area > 0.01
        for saved, expected in zip(
            cutout_polygons,
            expected_cutout_polygons,
            strict=True,
        )
    ):
        raise PLSplitError("OUTPUT_ENTITY_CONTRACT", "结果PL孔槽与预期边界不一致。")
    expected_label = f"p={developed.metadata.part_number}"
    if (
        len(label_entities) != 1
        or label_entities[0].dxftype() != "TEXT"
        or label_entities[0].dxf.text != expected_label
        or label_entities[0].dxf.style != "SplitChinese"
        or not any(
            abs(float(label_entities[0].dxf.height) - height) <= 1e-9
            for height in PL_LABEL_HEIGHT_OPTIONS_MM
        )
    ):
        raise PLSplitError(
            "OUTPUT_LABEL_CONTRACT",
            f"结果必须只有一个 SplitChinese 标签 {expected_label}。",
        )
    saved_polygon = validate_closed_outline(outer_entities)
    if any(
        not saved_polygon.covers(cutout_polygon) for cutout_polygon in cutout_polygons
    ):
        raise PLSplitError("OUTPUT_ENTITY_CONTRACT", "结果PL孔槽超出外轮廓。")
    if developed.longitudinal.selection_reason == "uniform_projection_fallback":
        expected_polygon = validate_closed_outline(developed.transformed_entities)
        if saved_polygon.symmetric_difference(expected_polygon).area > 0.01:
            raise PLSplitError(
                "OUTPUT_INTERVAL_CONTRACT",
                "0.1 mm内等比拉伸结果与保存后的原生边界不一致。",
            )
    else:
        _validate_saved_intervals(outer_entities, developed)
    native_bounds = bbox.extents(plate_entities, fast=False)
    if not native_bounds.has_data:
        raise PLSplitError("OUTPUT_ENTITY_CONTRACT", "结果 PLATE_CUT 没有有效范围。")
    min_x = float(native_bounds.extmin.x)
    min_y = float(native_bounds.extmin.y)
    max_x = float(native_bounds.extmax.x)
    max_y = float(native_bounds.extmax.y)
    length = max_x - min_x
    width = max_y - min_y
    if abs(length - developed.metrics.target_length_mm) > _DIMENSION_TOLERANCE_MM:
        raise _dimension_error("OUTPUT_LENGTH", "结果外轮廓长度与目标长度不一致。")
    if abs(width - developed.outline.width_mm) > _DIMENSION_TOLERANCE_MM:
        raise _dimension_error("OUTPUT_WIDTH", "结果外轮廓板宽发生变化。")
    if abs(min_x - developed.outline.anchor_x_mm) > _DIMENSION_TOLERANCE_MM:
        raise _dimension_error("OUTPUT_ANCHOR", "结果外轮廓左端锚点发生变化。")
    auditor = document.audit()
    if auditor.has_errors:
        raise PLSplitError(
            "OUTPUT_AUDIT",
            f"结果 DXF 审计发现 {len(auditor.errors)} 个错误。",
        )
    counts = Counter(entity.dxftype() for entity in modelspace)
    return PLWriteResult(
        output_path=target,
        min_x_mm=min_x,
        length_mm=length,
        width_mm=width,
        label=expected_label,
        entity_type_counts=tuple(sorted(counts.items())),
        audit_error_count=len(auditor.errors),
    )


def write_pl_dxf(
    developed: DevelopedPlate,
    output_path: str | Path,
) -> PLWriteResult:
    target = Path(output_path).resolve()
    document = ezdxf.new("R2007", setup=False)
    document.header["$INSUNITS"] = 4
    _ensure_layers(document)
    style = _ensure_style(document)
    modelspace = document.modelspace()
    manufacturing_entities = tuple(
        _manufacturing_clone(entity) for entity in developed.transformed_entities
    )
    developed_polygon = validate_closed_outline(manufacturing_entities)
    manufacturing_cutout_groups = tuple(
        tuple(_manufacturing_clone(entity) for entity in group)
        for group in developed.transformed_cutout_entity_groups
    )
    cutout_polygons = tuple(
        validate_closed_outline(group) for group in manufacturing_cutout_groups
    )
    for entity in manufacturing_entities:
        modelspace.add_entity(entity)
    for group in manufacturing_cutout_groups:
        for entity in group:
            modelspace.add_entity(entity)
    label = f"p={developed.metadata.part_number}"
    material_polygon = developed_polygon
    if cutout_polygons:
        material_polygon = developed_polygon.difference(unary_union(cutout_polygons))
    label_point: tuple[float, float] | None = None
    label_height: float | None = None
    try:
        placement = layout_part_marks(
            (
                PartMarkTarget(
                    target_id=developed.metadata.part_number,
                    label=label,
                    outer_geometry=developed_polygon,
                    material_geometry=material_polygon,
                    hole_count=len(cutout_polygons),
                ),
            ),
            preferred_height_mm=PL_LABEL_HEIGHT_MM,
        )[0]
        label_point = placement.point
        label_height = placement.height_mm
    except PartMarkLayoutError:
        pass
    if label_point is None:
        min_x, min_y, max_x, max_y = material_polygon.bounds
        center = ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)
        centroid = material_polygon.centroid
        representative = material_polygon.representative_point()
        point = polylabel(material_polygon, tolerance=0.1)
        candidates = (
            center,
            (float(centroid.x), float(centroid.y)),
            (float(representative.x), float(representative.y)),
            (float(point.x), float(point.y)),
            *(
                (
                    min_x + (max_x - min_x) * x_fraction,
                    min_y + (max_y - min_y) * y_fraction,
                )
                for y_fraction in (0.25, 0.5, 0.75)
                for x_fraction in (0.25, 0.5, 0.75)
            ),
        )
        for height in PL_LABEL_HEIGHT_OPTIONS_MM[1:]:
            for candidate in candidates:
                if material_polygon.covers(
                    part_mark_envelope(label, candidate, height)
                ):
                    label_point = candidate
                    label_height = height
                    break
            if label_point is not None:
                break
    if label_point is None or label_height is None:
        raise PLSplitError(
            "PL_LABEL_DOES_NOT_FIT",
            "10 mm零件标记仍无法完整放入板材区域。",
        )
    modelspace.add_text(
        label,
        height=label_height,
        dxfattribs={"layer": "PART_LABEL", "style": style},
    ).set_placement(label_point, align=TextEntityAlignment.MIDDLE_CENTER)
    auditor = document.audit()
    if auditor.has_errors:
        raise PLSplitError(
            "OUTPUT_AUDIT",
            f"保存前 DXF 审计发现 {len(auditor.errors)} 个错误。",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    document.saveas(target)
    return validate_saved_pl_dxf(target, developed)
