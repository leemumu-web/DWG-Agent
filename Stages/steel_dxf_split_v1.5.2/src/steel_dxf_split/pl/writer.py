from __future__ import annotations

from collections import Counter
from math import dist
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.entities import DXFEntity
from ezdxf.enums import TextEntityAlignment

from steel_dxf_split.dxf_io import load_document
from steel_dxf_split.part_mark_layout import (
    PartMarkLayoutError,
    PartMarkTarget,
    layout_part_marks,
)

from .contracts import DevelopedPlate, PLSplitError, PLWriteResult
from .geometry import flatten_entity, validate_closed_outline

_WINDOWS_CJK_DXF_FONT = "simsun.ttc"
_DIMENSION_TOLERANCE_MM = 0.001
PL_LABEL_HEIGHT_MM = 30.0


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


def _saved_endpoint_nodes(
    entities: tuple[DXFEntity, ...],
) -> tuple[tuple[float, float], ...]:
    nodes: list[tuple[float, float]] = []
    for entity in entities:
        points = flatten_entity(entity)
        for point in (points[0], points[-1]):
            if not any(
                dist(point, existing) <= _DIMENSION_TOLERANCE_MM for existing in nodes
            ):
                nodes.append(point)
    return tuple(nodes)


def _measure_station_xs(
    nodes: tuple[tuple[float, float], ...],
    expected_upper_x: float,
    expected_lower_x: float,
) -> tuple[float, float]:
    def matches(expected_x: float) -> tuple[tuple[float, float], ...]:
        return tuple(
            point
            for point in nodes
            if abs(point[0] - expected_x) <= _DIMENSION_TOLERANCE_MM
        )

    upper = matches(expected_upper_x)
    lower = matches(expected_lower_x)
    if not upper or not lower:
        raise _interval_error("结果外轮廓缺少源证明指定的展开站位。")
    if (
        abs(expected_upper_x - expected_lower_x) <= _DIMENSION_TOLERANCE_MM
        and len(upper) < 2
    ):
        raise _interval_error("结果外轮廓的上下链没有同时保留展开站位。")
    measured_upper = min(upper, key=lambda point: abs(point[0] - expected_upper_x))[0]
    measured_lower = min(lower, key=lambda point: abs(point[0] - expected_lower_x))[0]
    return measured_upper, measured_lower


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
    expected_upper = [float(proof_intervals[0].left_station.upper_x_mm)]
    expected_lower = [float(proof_intervals[0].left_station.lower_x_mm)]
    for interval in interval_metrics:
        expected_upper.append(expected_upper[-1] + interval.output_upper_span_mm)
        expected_lower.append(expected_lower[-1] + interval.output_lower_span_mm)
    nodes = _saved_endpoint_nodes(plate_entities)
    measured = tuple(
        _measure_station_xs(nodes, upper, lower)
        for upper, lower in zip(expected_upper, expected_lower, strict=True)
    )
    for index, interval in enumerate(interval_metrics):
        measured_upper_span = measured[index + 1][0] - measured[index][0]
        measured_lower_span = measured[index + 1][1] - measured[index][1]
        if (
            abs(measured_upper_span - interval.output_upper_span_mm)
            > _DIMENSION_TOLERANCE_MM
            or abs(measured_lower_span - interval.output_lower_span_mm)
            > _DIMENSION_TOLERANCE_MM
        ):
            raise _interval_error("结果外轮廓的展开区间跨度与审计指标不一致。")


def validate_saved_pl_dxf(
    output_path: str | Path,
    developed: DevelopedPlate,
) -> PLWriteResult:
    target = Path(output_path).resolve()
    try:
        document = load_document(target)
    except Exception as error:
        raise PLSplitError("OUTPUT_LOAD_FAILED", f"结果 DXF 无法重新审计读取：{error}") from error
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
    expected_label = f"p={developed.metadata.part_number}"
    if (
        len(label_entities) != 1
        or label_entities[0].dxftype() != "TEXT"
        or label_entities[0].dxf.text != expected_label
        or label_entities[0].dxf.style != "SplitChinese"
        or abs(float(label_entities[0].dxf.height) - PL_LABEL_HEIGHT_MM) > 1e-9
    ):
        raise PLSplitError(
            "OUTPUT_LABEL_CONTRACT",
            f"结果必须只有一个 SplitChinese 标签 {expected_label}。",
        )
    validate_closed_outline(plate_entities)
    _validate_saved_intervals(plate_entities, developed)
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
    for entity in manufacturing_entities:
        modelspace.add_entity(entity)
    label = f"p={developed.metadata.part_number}"
    try:
        placement = layout_part_marks(
            (
                PartMarkTarget(
                    target_id=developed.metadata.part_number,
                    label=label,
                    outer_geometry=developed_polygon,
                    material_geometry=developed_polygon,
                ),
            ),
            preferred_height_mm=PL_LABEL_HEIGHT_MM,
        )[0]
    except PartMarkLayoutError as error:
        raise PLSplitError(
            "PL_LABEL_DOES_NOT_FIT",
            "30 mm零件标记无法完整放入板材区域。",
        ) from error
    if abs(placement.height_mm - PL_LABEL_HEIGHT_MM) > 1e-9:
        raise PLSplitError(
            "PL_LABEL_DOES_NOT_FIT",
            "30 mm零件标记无法完整放入板材区域。",
        )
    modelspace.add_text(
        label,
        height=placement.height_mm,
        dxfattribs={"layer": "PART_LABEL", "style": style},
    ).set_placement(placement.point, align=TextEntityAlignment.MIDDLE_CENTER)
    auditor = document.audit()
    if auditor.has_errors:
        raise PLSplitError(
            "OUTPUT_AUDIT",
            f"保存前 DXF 审计发现 {len(auditor.errors)} 个错误。",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    document.saveas(target)
    return validate_saved_pl_dxf(target, developed)
