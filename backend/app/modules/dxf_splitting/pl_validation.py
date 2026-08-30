"""Independent saved-DXF validation for the PL production Stage."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from typing import Any

import ezdxf
from ezdxf import path as ezdxf_path
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize_full, unary_union

from app.modules.dxf_splitting.validation import StagedSplitSource, ValidatedSplitItem

_TENTH_MM = Decimal("0.1")
_DIMENSION_TOLERANCE_MM = 0.1
_SUPPORTED_CUT_ENTITIES = {
    "ARC",
    "CIRCLE",
    "ELLIPSE",
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "SPLINE",
}


def _manual(
    source: StagedSplitSource,
    *,
    disposition: str,
    diagnostics: list[str],
    findings: list[str],
    output_path: Path | None = None,
) -> ValidatedSplitItem:
    return ValidatedSplitItem(
        source=source,
        family="PL",
        automation_route="manual_review",
        disposition=disposition,
        normal_dxf_path=output_path,
        weld_allowance_dxf_path=None,
        split_report_path=None,
        weld_allowance_report_path=None,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
        validation={
            "status": "manual_review",
            "checks": {},
            "findings": findings,
        },
    )


def _resolved_output(value: object, output_root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("PL 结果路径缺失。")
    path = Path(value)
    resolved = path.resolve()
    try:
        resolved.relative_to(output_root.resolve())
    except ValueError as exc:
        raise ValueError("PL 结果路径越出本次输出目录。") from exc
    if path.is_symlink() or not resolved.is_file():
        raise ValueError("PL 结果文件缺失或不是普通文件。")
    return resolved


def _positive_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label}不是有效毫米值。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}不是有效毫米值。") from exc
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{label}不是正的有限毫米值。")
    return number


def _flatten_cut_entity(entity) -> LineString:
    if entity.dxftype() == "LINE":
        vertices = (entity.dxf.start, entity.dxf.end)
    else:
        try:
            vertices = tuple(
                ezdxf_path.make_path(entity).flattening(distance=0.01, segments=16)
            )
        except Exception as exc:
            raise ValueError(f"不支持读取 {entity.dxftype()} 制造边界。") from exc
    coordinates = tuple((float(vertex.x), float(vertex.y)) for vertex in vertices)
    if len(coordinates) < 2:
        raise ValueError("PLATE_CUT 含退化制造边界。")
    return LineString(coordinates)


def _material_polygon(entities: tuple[object, ...]) -> Polygon:
    if not entities or any(
        entity.dxftype() not in _SUPPORTED_CUT_ENTITIES for entity in entities
    ):
        raise ValueError("PLATE_CUT 缺少边界或含不支持的原生实体。")
    linework = unary_union([_flatten_cut_entity(entity) for entity in entities])
    polygons, cuts, dangles, invalid = polygonize_full(linework)
    if not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
        raise ValueError("PLATE_CUT 不能重建为完整闭合边界。")
    rings = [polygon for polygon in polygons.geoms if polygon.area > 1e-6]
    if not rings:
        raise ValueError("PLATE_CUT 没有有效闭合材料轮廓。")
    outer = max(rings, key=lambda polygon: polygon.envelope.area)
    shell = Polygon(outer.exterior)
    holes = []
    for ring in rings:
        if ring is outer:
            continue
        candidate = Polygon(ring.exterior)
        if not shell.covers(candidate):
            raise ValueError("PLATE_CUT 含多个互不从属的外轮廓。")
        holes.append(tuple(candidate.exterior.coords))
    material = Polygon(tuple(shell.exterior.coords), holes)
    if not material.is_valid or material.is_empty or material.area <= 1e-6:
        raise ValueError("PLATE_CUT 材料多边形无效。")
    return material


def _upward_tenth(raw_mm: float) -> float:
    try:
        return float(Decimal(str(raw_mm)).quantize(_TENTH_MM, rounding=ROUND_CEILING))
    except InvalidOperation as exc:
        raise ValueError("PL 目标长度无法按 0.1 mm 向上取整。") from exc


def validate_pl_result(
    source: StagedSplitSource,
    report_item: dict[str, Any],
    output_root: Path,
) -> ValidatedSplitItem:
    """Validate one Stage result without importing the PL implementation package."""
    if report_item.get("status") == "rejected":
        error = report_item.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message_zh") if isinstance(error, dict) else None
        return _manual(
            source,
            disposition="stage_rejected",
            diagnostics=["PL_STAGE_REJECTED", str(code or "PL_REJECTED")],
            findings=[str(message or "PL Stage 安全拒绝。")],
        )
    if report_item.get("status") != "success":
        return _manual(
            source,
            disposition="invalid_stage_result",
            diagnostics=["PL_STAGE_RESULT_INVALID"],
            findings=["PL Stage 返回了未知的逐图状态。"],
        )

    diagnostics: list[str] = []
    findings: list[str] = []
    output_path: Path | None = None
    output = report_item.get("output")
    metadata = report_item.get("metadata")
    lengths = report_item.get("lengths")
    if not isinstance(output, dict) or not isinstance(metadata, dict) or not isinstance(lengths, dict):
        return _manual(
            source,
            disposition="independent_validation_failed",
            diagnostics=["PL_STAGE_RESULT_INVALID"],
            findings=["PL Stage 成功项缺少尺寸或输出证据。"],
        )
    try:
        output_path = _resolved_output(output.get("path"), output_root)
    except ValueError as exc:
        return _manual(
            source,
            disposition="independent_validation_failed",
            diagnostics=["PL_OUTPUT_MISSING"],
            findings=[str(exc)],
        )

    try:
        document = ezdxf.readfile(output_path)
        auditor = document.audit()
    except Exception as exc:
        return _manual(
            source,
            disposition="independent_validation_failed",
            diagnostics=["PL_OUTPUT_DXF_UNREADABLE"],
            findings=[f"PL 结果无法独立重开：{exc.__class__.__name__}"],
        )
    if auditor.has_errors:
        diagnostics.append("PL_OUTPUT_AUDIT_FAILED")
        findings.append(f"PL 结果 DXF 审计发现 {len(auditor.errors)} 个错误。")
    if int(document.header.get("$INSUNITS", 0)) != 4:
        diagnostics.append("PL_OUTPUT_UNITS_INVALID")
        findings.append("PL 结果单位不是毫米。")

    modelspace = tuple(document.modelspace())
    cut_entities = tuple(
        entity for entity in modelspace if entity.dxf.layer == "PLATE_CUT"
    )
    label_entities = tuple(
        entity for entity in modelspace if entity.dxf.layer == "PART_LABEL"
    )
    if len(modelspace) != len(cut_entities) + len(label_entities):
        diagnostics.append("PL_OUTPUT_ENTITY_CONTRACT_INVALID")
        findings.append("PL 结果含 PLATE_CUT/PART_LABEL 之外的模型空间实体。")
    part_number = report_item.get("part_number")
    expected_label = f"p={part_number}" if isinstance(part_number, str) and part_number else None
    if (
        expected_label is None
        or len(label_entities) != 1
        or label_entities[0].dxftype() != "TEXT"
        or str(label_entities[0].dxf.text) != expected_label
        or output.get("label") != expected_label
    ):
        diagnostics.append("PL_OUTPUT_LABEL_INVALID")
        findings.append("PL 结果必须只有一个与零件号一致的 p= 前缀标签。")

    material: Polygon | None = None
    try:
        material = _material_polygon(cut_entities)
    except ValueError as exc:
        diagnostics.append("PL_OUTPUT_OUTLINE_INVALID")
        findings.append(str(exc))

    checks: dict[str, object] = {
        "dxf_reopen": True,
        "audit_error_count": len(auditor.errors),
        "millimetre_units": int(document.header.get("$INSUNITS", 0)) == 4,
        "exact_part_label": "PL_OUTPUT_LABEL_INVALID" not in diagnostics,
        "single_material_polygon": material is not None,
        "normal_output_only": True,
    }
    try:
        projection = _positive_number(lengths.get("projection_mm"), "主视图投影长度")
        bom = _positive_number(lengths.get("bom_mm"), "材料表长度")
        k_value = lengths.get("k_length_mm")
        k_length = projection if k_value is None else _positive_number(k_value, "K=0.5长度")
        raw = _positive_number(lengths.get("raw_mm"), "展开原始长度")
        target = _positive_number(lengths.get("target_mm"), "展开目标长度")
        expected_raw = max(projection, bom, k_length)
        expected_target = _upward_tenth(expected_raw)
        target_is_upward_tenth = (
            abs(raw - expected_raw) <= 1e-6 and abs(target - expected_target) <= 1e-6
        )
        checks["target_is_upward_tenth"] = target_is_upward_tenth
        if not target_is_upward_tenth:
            diagnostics.append("PL_TARGET_ROUNDING_INVALID")
            findings.append("PL 目标长度不是全部来源要求最大值的 0.1 mm 向上取整。")
        declared_width = _positive_number(metadata.get("width_mm"), "PL 板宽")
        if material is not None:
            min_x, min_y, max_x, max_y = material.envelope.bounds
            actual_length = max_x - min_x
            actual_width = max_y - min_y
            checks["actual_length_mm"] = actual_length
            checks["actual_width_mm"] = actual_width
            checks["target_length_mm"] = target
            checks["declared_width_mm"] = declared_width
            if actual_length + 1e-6 < target:
                diagnostics.append("PL_OUTPUT_LENGTH_DOWNWARD")
                findings.append("PL 结果长度发生向下误差。")
            elif actual_length - target > _DIMENSION_TOLERANCE_MM + 1e-6:
                diagnostics.append("PL_OUTPUT_LENGTH_TOO_LONG")
                findings.append("PL 结果长度超过目标 0.1 mm 以上。")
            if abs(actual_width - declared_width) > _DIMENSION_TOLERANCE_MM + 1e-6:
                diagnostics.append("PL_OUTPUT_WIDTH_MISMATCH")
                findings.append("PL 结果板宽与来源要求误差超过 0.1 mm。")
    except ValueError as exc:
        diagnostics.append("PL_STAGE_DIMENSION_EVIDENCE_INVALID")
        findings.append(str(exc))

    if diagnostics:
        return _manual(
            source,
            disposition="independent_validation_failed",
            diagnostics=diagnostics,
            findings=findings,
            output_path=output_path,
        )
    checks["output_never_shorter"] = True
    checks["width_within_0_1_mm"] = True
    return ValidatedSplitItem(
        source=source,
        family="PL",
        automation_route="auto_accepted",
        disposition="auto_accepted",
        normal_dxf_path=output_path,
        weld_allowance_dxf_path=None,
        split_report_path=None,
        weld_allowance_report_path=None,
        diagnostics=(),
        validation={"status": "passed", "checks": checks, "findings": []},
    )
