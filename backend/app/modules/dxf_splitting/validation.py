"""Independent platform validation for one immutable split attempt."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ezdxf

from app.modules.dxf_classification.interface import DxfNextStageInput
from app.modules.dxf_splitting.adapter import (
    VALIDATION_SCHEMA,
    DxfSplitError,
    member_name,
    source_contract_for,
)
from app.modules.files.interface import validate_dxf_structure


@dataclass(frozen=True)
class StagedSplitSource:
    semantic: DxfNextStageInput
    source_name: str
    staged_path: Path


@dataclass(frozen=True)
class ValidatedSplitItem:
    source: StagedSplitSource
    family: str | None
    automation_route: str
    disposition: str
    normal_dxf_path: Path | None
    weld_allowance_dxf_path: Path | None
    split_report_path: Path | None
    weld_allowance_report_path: Path | None
    diagnostics: tuple[str, ...]
    validation: dict[str, object]


def _resolved_output(value: object, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} 路径缺失")
    path = Path(value)
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} 路径越出本次输出目录") from exc
    if path.is_symlink() or not resolved.is_file():
        raise ValueError(f"{label} 文件缺失或不是普通文件")
    return resolved


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} 不是合法 UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 顶层不是对象")
    return payload


def _validate_dxf(path: Path, label: str) -> None:
    try:
        payload = path.read_bytes()
        validate_dxf_structure(payload)
        document = ezdxf.readfile(path)
        tuple(document.modelspace())
    except Exception as exc:
        raise ValueError(f"{label} 无法独立重开校验") from exc


def _manual_item(
    source: StagedSplitSource,
    *,
    family: str | None,
    disposition: str,
    diagnostics: list[str] | tuple[str, ...],
    checks: dict[str, object] | None = None,
    normal_dxf_path: Path | None = None,
    weld_allowance_dxf_path: Path | None = None,
    split_report_path: Path | None = None,
    weld_allowance_report_path: Path | None = None,
) -> ValidatedSplitItem:
    return ValidatedSplitItem(
        source=source,
        family=family,
        automation_route="manual_review",
        disposition=disposition,
        normal_dxf_path=normal_dxf_path,
        weld_allowance_dxf_path=weld_allowance_dxf_path,
        split_report_path=split_report_path,
        weld_allowance_report_path=weld_allowance_report_path,
        diagnostics=tuple(dict.fromkeys(str(value) for value in diagnostics if value)),
        validation={
            "status": "manual_review",
            "checks": checks or {},
        },
    )


def unsupported_split_item(source: StagedSplitSource) -> ValidatedSplitItem:
    return _manual_item(
        source,
        family=None,
        disposition="unsupported_part_type",
        diagnostics=("UNSUPPORTED_SPLIT_PART_TYPE",),
        checks={
            "part_type": source.semantic.part_type,
            "supported_part_types": ["BH", "BOX"],
        },
    )


def _validate_auto_result(
    source: StagedSplitSource,
    result: dict[str, Any],
    output_root: Path,
) -> ValidatedSplitItem:
    findings: list[str] = []
    paths: dict[str, Path] = {}
    path_fields = {
        "normal_dxf": ("production_clean", "正常拆板 DXF"),
        "weld_allowance_dxf": ("weld_allowance", "余量增长 DXF"),
        "split_report": ("report", "拆板报告"),
        "weld_allowance_report": ("weld_allowance_report", "余量增长报告"),
    }
    for key, (field, label) in path_fields.items():
        try:
            paths[key] = _resolved_output(result.get(field), output_root, label)
        except ValueError as exc:
            findings.append(str(exc))

    expected_member = member_name(source.source_name)
    normal = paths.get("normal_dxf")
    allowance = paths.get("weld_allowance_dxf")
    if normal is not None and normal.name != f"{expected_member}_正常拆板.dxf":
        findings.append("正常拆板 DXF 文件名不符合中文后缀契约")
    if allowance is not None and allowance.name != f"{expected_member}_余量增长.dxf":
        findings.append("余量增长 DXF 文件名不符合中文后缀契约")
    if normal is not None and allowance is not None and normal == allowance:
        findings.append("正常拆板与余量增长 DXF 不能指向同一文件")
    family = result.get("family")
    if family != source.semantic.part_type:
        findings.append("拆板识别族与分类类型不一致")

    candidate_pair_readable = normal is not None and allowance is not None
    for path, label in (
        (normal, "正常拆板 DXF"),
        (allowance, "余量增长 DXF"),
    ):
        if path is None:
            continue
        try:
            _validate_dxf(path, label)
        except ValueError as exc:
            findings.append(str(exc))
            candidate_pair_readable = False

    report: dict[str, Any] | None = None
    allowance_report: dict[str, Any] | None = None
    split_report_path = paths.get("split_report")
    allowance_report_path = paths.get("weld_allowance_report")
    if split_report_path is not None:
        try:
            report = _read_json_object(split_report_path, "拆板报告")
        except ValueError as exc:
            findings.append(str(exc))
    if allowance_report_path is not None:
        try:
            allowance_report = _read_json_object(
                allowance_report_path,
                "余量增长报告",
            )
        except ValueError as exc:
            findings.append(str(exc))
    paired = report.get("paired_output") if isinstance(report, dict) else None
    if not isinstance(paired, dict) or paired.get("status") != "auto_accepted":
        findings.append("拆板报告缺少成对自动验收结论")
    elif normal is not None and allowance is not None:
        for field, expected, label in (
            ("normal_dxf", normal, "拆板报告中的正常拆板 DXF"),
            ("weld_allowance_dxf", allowance, "拆板报告中的余量增长 DXF"),
        ):
            try:
                declared = _resolved_output(paired.get(field), output_root, label)
            except ValueError as exc:
                findings.append(str(exc))
            else:
                if declared != expected:
                    findings.append(f"{label}与逐图结果不一致")
    if isinstance(report, dict) and report.get("automation_route") != "auto_accepted":
        findings.append("拆板报告的自动化路由不是 auto_accepted")
    if not isinstance(allowance_report, dict):
        findings.append("余量增长报告内容缺失")

    task_dir_value = result.get("task_dir")
    try:
        task_dir = Path(str(task_dir_value)).resolve()
        task_dir.relative_to(output_root.resolve())
        if Path(str(task_dir_value)).is_symlink() or not task_dir.is_dir():
            raise ValueError
        task_dxfs = tuple(path.resolve() for path in task_dir.rglob("*.dxf") if path.is_file())
        if len(task_dxfs) != 2:
            findings.append("自动任务目录不包含且仅包含一对 DXF")
        elif (
            normal is not None
            and allowance is not None
            and set(task_dxfs)
            != {
                normal,
                allowance,
            }
        ):
            findings.append("自动任务目录中的 DXF 与逐图成对结果不一致")
    except (OSError, ValueError):
        findings.append("自动任务目录无效")

    if findings:
        return _manual_item(
            source,
            family=str(family) if isinstance(family, str) else None,
            disposition="independent_validation_failed",
            diagnostics=["INDEPENDENT_VALIDATION_FAILED", *findings],
            checks={"findings": findings},
            normal_dxf_path=normal if candidate_pair_readable else None,
            weld_allowance_dxf_path=allowance if candidate_pair_readable else None,
            split_report_path=(
                split_report_path if candidate_pair_readable and report is not None else None
            ),
            weld_allowance_report_path=(
                allowance_report_path
                if candidate_pair_readable and allowance_report is not None
                else None
            ),
        )
    return ValidatedSplitItem(
        source=source,
        family=str(family),
        automation_route="auto_accepted",
        disposition=str(result.get("disposition") or "auto_accepted"),
        normal_dxf_path=normal,
        weld_allowance_dxf_path=allowance,
        split_report_path=split_report_path,
        weld_allowance_report_path=allowance_report_path,
        diagnostics=tuple(
            str(value) for value in result.get("diagnostic_codes", []) if isinstance(value, str)
        ),
        validation={
            "status": "passed",
            "checks": {
                "family_matches_classification": True,
                "exact_output_suffixes": True,
                "paired_dxf_count": 2,
                "dxf_reopen": True,
                "reports_readable": True,
            },
        },
    )


def validate_split_results(
    sources: list[StagedSplitSource],
    cli_payload: dict[str, Any],
    output_root: Path,
) -> list[ValidatedSplitItem]:
    """Match every supported input exactly once, then validate all outputs."""
    by_path = {source.staged_path.resolve(): source for source in sources}
    if len(by_path) != len(sources):
        raise DxfSplitError("拆板输入暂存路径不唯一。")
    results = cli_payload.get("results")
    if not isinstance(results, list):
        raise DxfSplitError("拆板适配层缺少逐图结果。")
    validated: list[ValidatedSplitItem] = []
    seen: set[Path] = set()
    for raw_result in results:
        if not isinstance(raw_result, dict):
            raise DxfSplitError("拆板逐图结果格式无效。")
        input_value = raw_result.get("input")
        if not isinstance(input_value, str):
            raise DxfSplitError("拆板逐图结果缺少输入路径。")
        input_path = Path(input_value).resolve()
        source = by_path.get(input_path)
        if source is None or input_path in seen:
            raise DxfSplitError("拆板逐图结果无法与冻结输入一一对应。")
        seen.add(input_path)
        route = raw_result.get("automation_route")
        if route == "auto_accepted":
            validated.append(_validate_auto_result(source, raw_result, output_root))
        elif route == "manual_review":
            diagnostics = [
                str(value)
                for value in raw_result.get("diagnostic_codes", [])
                if isinstance(value, str)
            ]
            validated.append(
                _manual_item(
                    source,
                    family=(
                        str(raw_result.get("family"))
                        if isinstance(raw_result.get("family"), str)
                        else None
                    ),
                    disposition=str(raw_result.get("disposition") or "manual_review"),
                    diagnostics=["SPLITTER_MANUAL_REVIEW", *diagnostics],
                )
            )
        else:
            raise DxfSplitError("拆板逐图结果包含未知业务路由。")
    if seen != set(by_path):
        raise DxfSplitError("拆板逐图结果遗漏冻结输入。")
    return validated


def build_validation_report(
    *,
    workflow_id: int,
    split_run_id: int,
    job_attempt: int,
    input_manifest_sha256: str,
    items: list[ValidatedSplitItem],
) -> dict[str, object]:
    manual_count = sum(item.automation_route == "manual_review" for item in items)
    return {
        "schema": VALIDATION_SCHEMA,
        "workflow_id": workflow_id,
        "split_run_id": split_run_id,
        "job_attempt": job_attempt,
        "input_manifest_sha256": input_manifest_sha256,
        "status": "completed_with_review" if manual_count else "completed",
        "input_count": len(items),
        "auto_accepted_count": len(items) - manual_count,
        "manual_review_count": manual_count,
        "results": [
            {
                "classification_item_id": item.source.semantic.classification_item_id,
                "source_file_id": item.source.semantic.output_file_id,
                "source_name": item.source.source_name,
                "part_type": item.source.semantic.part_type,
                "source_contract_id": source_contract_for(item.source.semantic.part_type),
                "family": item.family,
                "automation_route": item.automation_route,
                "disposition": item.disposition,
                "diagnostics": list(item.diagnostics),
                "validation": item.validation,
            }
            for item in items
        ],
    }
