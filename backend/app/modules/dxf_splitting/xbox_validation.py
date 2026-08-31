"""Independent saved-DXF validation for the XBOX production Stage.

Unlike the PL single-artifact rule, a certified XBOX result is a pair:
the normal split DXF plus its weld-allowance extension. Both artifacts are
reopened here without importing the Stage implementation package.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import ezdxf

from app.modules.dxf_splitting.validation import StagedSplitSource, ValidatedSplitItem

_DIMENSION_TOLERANCE_MM = 0.1
_PLATE_VERTEX_COUNT = 4
_EXPECTED_PLATE_COUNT = 2
_PROFILE_PATTERN = re.compile(
    r"^XBOX(?P<height>\d+(?:\.\d+)?)\*(?P<width>\d+(?:\.\d+)?)"
    r"\*(?P<web>\d+(?:\.\d+)?)\*(?P<flange>\d+(?:\.\d+)?)"
    r"(?:\*\d+(?:\.\d+)?)?$"
)

# Weld-allowance extension tiers (upper bound inclusive) in millimetres.
_ALLOWANCE_TIERS: tuple[tuple[int, int], ...] = (
    (2000, 0),
    (5000, 5),
    (10000, 10),
    (15000, 15),
)


def _allowance_increment(length_mm: float) -> int:
    for bound, increment in _ALLOWANCE_TIERS:
        if length_mm <= bound:
            return increment
    return 20


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
        family="XBOX",
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


def _resolved_output(value: object, output_root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}结果路径缺失。")
    path = Path(value)
    if not path.is_absolute():
        # Stage batch reports carry paths relative to the batch output dir.
        path = output_root / path
    resolved = path.resolve()
    try:
        resolved.relative_to(output_root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label}结果路径越出本次输出目录。") from exc
    if path.is_symlink() or not resolved.is_file():
        raise ValueError(f"{label}结果文件缺失或不是普通文件。")
    return resolved


def _plate_boxes(path: Path) -> list[tuple[float, float, int, bool]]:
    document = ezdxf.readfile(path)
    plates: list[tuple[float, float, int, bool]] = []
    for entity in document.modelspace():
        if entity.dxftype() != "LWPOLYLINE":
            continue
        vertices = tuple(
            (float(point[0]), float(point[1])) for point in entity.get_points("xy")
        )
        xs = [vertex[0] for vertex in vertices]
        ys = [vertex[1] for vertex in vertices]
        plates.append(
            (
                max(xs) - min(xs),
                max(ys) - min(ys),
                len(vertices),
                bool(entity.closed),
            )
        )
    return plates


def _labels(path: Path) -> list[str]:
    document = ezdxf.readfile(path)
    return [
        str(entity.dxf.text)
        for entity in document.modelspace()
        if entity.dxftype() == "TEXT"
    ]


def _reopen(path: Path, label: str) -> ezdxf.document.Drawing:
    try:
        document = ezdxf.readfile(path)
        auditor = document.audit()
    except Exception as exc:
        raise ValueError(f"{label} DXF 无法独立重开：{exc.__class__.__name__}") from exc
    if auditor.has_errors:
        raise ValueError(f"{label} DXF 审计发现 {len(auditor.errors)} 个错误。")
    if int(document.header.get("$INSUNITS", 0)) != 4:
        raise ValueError(f"{label} DXF 单位不是毫米。")
    unsupported = {
        entity.dxftype()
        for entity in document.modelspace()
        if entity.dxftype() not in {"LWPOLYLINE", "TEXT"}
    }
    if unsupported:
        raise ValueError(f"{label} DXF 含契约之外的实体类型：{sorted(unsupported)}")
    return document


def validate_xbox_result(
    source: StagedSplitSource,
    report_item: dict[str, Any],
    output_root: Path,
) -> ValidatedSplitItem:
    """Validate one XBOX Stage pair without importing the implementation package."""
    if report_item.get("status") != "auto_accepted":
        error = report_item.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message_zh") if isinstance(error, dict) else None
        diagnostics = (
            ["XBOX_STAGE_REJECTED", str(code or "XBOX_REJECTED")]
            if report_item.get("status") == "manual_review"
            else ["XBOX_STAGE_RESULT_INVALID"]
        )
        finding = str(message or "XBOX Stage 未能形成正式拆板结果。")
        return _manual(
            source,
            disposition=(
                "stage_rejected"
                if report_item.get("status") == "manual_review"
                else "invalid_stage_result"
            ),
            diagnostics=diagnostics,
            findings=[finding],
        )

    outputs = report_item.get("outputs")
    if not isinstance(outputs, dict):
        return _manual(
            source,
            disposition="independent_validation_failed",
            diagnostics=["XBOX_STAGE_RESULT_INVALID"],
            findings=["XBOX Stage 成功项缺少产物清单。"],
        )
    diagnostics: list[str] = []
    findings: list[str] = []
    try:
        normal_path = _resolved_output(
            outputs.get("normal_dxf"), output_root, "XBOX 正常拆板"
        )
        weld_path = _resolved_output(
            outputs.get("weld_allowance_dxf"), output_root, "XBOX 焊接余量"
        )
        report_json = _resolved_output(
            outputs.get("report"), output_root, "XBOX 拆板报告"
        )
        weld_report_json = _resolved_output(
            outputs.get("weld_allowance_report"),
            output_root,
            "XBOX 焊接余量报告",
        )
    except ValueError as exc:
        return _manual(
            source,
            disposition="independent_validation_failed",
            diagnostics=["XBOX_OUTPUT_MISSING"],
            findings=[str(exc)],
        )

    try:
        normal_document = _reopen(normal_path, "XBOX 正常拆板")
        weld_document = _reopen(weld_path, "XBOX 焊接余量")
    except ValueError as exc:
        return _manual(
            source,
            disposition="independent_validation_failed",
            diagnostics=["XBOX_OUTPUT_DXF_UNREADABLE"],
            findings=[str(exc)],
        )
    del normal_document, weld_document

    member = report_item.get("member")
    expected_labels = sorted(
        [
            f"p={member}\\U+8179",
            f"p={member}\\U+7FFC",
        ]
    )
    normal_plates = _plate_boxes(normal_path)
    weld_plates = _plate_boxes(weld_path)
    checks: dict[str, object] = {
        "dxf_reopen": True,
        "millimetre_units": True,
        "entity_contract": True,
    }
    if len(normal_plates) != _EXPECTED_PLATE_COUNT or len(weld_plates) != (
        _EXPECTED_PLATE_COUNT
    ):
        diagnostics.append("XBOX_PLATE_COUNT_INVALID")
        findings.append(
            f"XBOX 结果必须恰好有 {_EXPECTED_PLATE_COUNT} 块板件轮廓："
            f"正常版 {len(normal_plates)} 块、余量版 {len(weld_plates)} 块。"
        )
    else:
        checks["plate_count"] = True
        for index, (_length, _width, vertices, closed) in enumerate(normal_plates):
            if vertices != _PLATE_VERTEX_COUNT or not closed:
                diagnostics.append("XBOX_OUTLINE_INVALID")
                findings.append(
                    f"XBOX 正常拆板第 {index} 块板不是闭合四顶点轮廓。"
                )
        if "XBOX_OUTLINE_INVALID" not in diagnostics:
            checks["closed_rectangular_plates"] = True
        actual_labels = sorted(_labels(normal_path))
        if actual_labels != expected_labels:
            diagnostics.append("XBOX_LABEL_INVALID")
            findings.append(
                "XBOX 结果标签必须是 p=<零件号>腹/翼 两条。"
            )
        else:
            checks["exact_web_flange_labels"] = True

        # Weld-allowance proof: same widths, lengths extended by the tier value.
        for index, ((length, width, _, _), (weld_length, weld_width, _, _)) in (
            enumerate(zip(normal_plates, weld_plates, strict=True))
        ):
            if abs(weld_width - width) > _DIMENSION_TOLERANCE_MM + 1e-6:
                diagnostics.append("XBOX_ALLOWANCE_WIDTH_CHANGED")
                findings.append(
                    f"XBOX 余量版第 {index} 块板宽度发生改变：{width} -> {weld_width}。"
                )
                continue
            expected = _allowance_increment(length)
            if abs((weld_length - length) - expected) > _DIMENSION_TOLERANCE_MM + 1e-6:
                diagnostics.append("XBOX_ALLOWANCE_TIER_MISMATCH")
                findings.append(
                    f"XBOX 余量版第 {index} 块板长度增量不符档位："
                    f"预期 +{expected}mm，实际 +{round(weld_length - length, 3)}mm。"
                )
        if not (
            {"XBOX_ALLOWANCE_WIDTH_CHANGED", "XBOX_ALLOWANCE_TIER_MISMATCH"}
            & set(diagnostics)
        ):
            checks["weld_allowance_tiers_proven"] = True

        # Cross-check plate widths against the normalized XBOX profile.
        profile = source.semantic.profile_normalized or ""
        match = _PROFILE_PATTERN.match(profile.replace(" ", ""))
        if match is not None:
            height = float(match.group("height"))
            width_spec = float(match.group("width"))
            web = float(match.group("web"))
            flange = float(match.group("flange"))
            expected_widths = sorted([height - 2 * flange, width_spec - 2 * web])
            actual_widths = sorted(plate[1] for plate in normal_plates)
            if any(
                abs(e - a) > _DIMENSION_TOLERANCE_MM + 1e-6
                for e, a in zip(expected_widths, actual_widths, strict=True)
            ):
                diagnostics.append("XBOX_PROFILE_WIDTH_MISMATCH")
                findings.append(
                    "XBOX 板件宽度与规范化规格推导值不一致："
                    f"预期 {expected_widths}，实际 {actual_widths}。"
                )
            else:
                checks["profile_widths_match"] = True

    if diagnostics:
        return _manual(
            source,
            disposition="independent_validation_failed",
            diagnostics=diagnostics,
            findings=findings,
            output_path=normal_path,
        )
    return ValidatedSplitItem(
        source=source,
        family="XBOX",
        automation_route="auto_accepted",
        disposition="auto_accepted",
        normal_dxf_path=normal_path,
        weld_allowance_dxf_path=weld_path,
        split_report_path=report_json,
        weld_allowance_report_path=weld_report_json,
        diagnostics=(),
        validation={"status": "passed", "checks": checks, "findings": []},
    )


__all__ = ["validate_xbox_result"]
