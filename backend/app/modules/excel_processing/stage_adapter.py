from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pymysql

from app.modules.excel_processing.schemas import (
    ExcelInputFailure,
    ExcelInputIssue,
    ExcelStage1Inspection,
    HandbookCategory,
)
from app.platform.config.settings import settings

logger = logging.getLogger(__name__)

_RESULT_PREFIX = "DWG_EXCEL_FINAL_RESULT="
_ERROR_PREFIX = "DWG_EXCEL_FINAL_ERROR="
_PROTOCOL_VERSION = 1
_INPUT_CONTRACT_VERSION = 1
_REQUIRED_STAGE_FILES = (
    "main.py",
    "pipeline.py",
    "handbook.py",
    "config.py",
    "material_routing.py",
    "bh_stage2.py",
    "box_stage2.py",
    "stage2_workbook.py",
)
_QUALITY_STATUSES = {"ok", "warning", "severe_warning"}
_LOOKUP_STATUSES = {"hit", "not_found", "skipped", "conflict"}
_LOOKUP_CATEGORIES = {item.value for item in HandbookCategory}
_D_MATERIAL_CATEGORY_BY_PREFIX = {
    "HRB": "rebar",
    "HPB": "round_bar",
    "Q235B": "round_bar",
    "Q355B": "round_bar",
}
_CIRCULAR_HOLLOW_RE = re.compile(
    r"(PIP|PD)(\d+(?:\.\d+)?)\*(\d+(?:\.\d+)?)",
    flags=re.IGNORECASE,
)
_PROCESS_RESULT_FIELDS = {
    "protocol_version",
    "operation",
    "output_path",
    "quality_status",
    "warning_count",
    "severe_warning_count",
    "report_summary",
}
_PROCESS_STAGE2_RESULT_FIELDS = _PROCESS_RESULT_FIELDS | {
    "status",
    "matched_occurrence_count",
    "missing_drawing_count",
    "unmatched_drawing_count",
    "manual_occurrence_count",
}
_LOOKUP_RESULT_FIELDS = {
    "protocol_version",
    "operation",
    "category",
    "normalized_spec",
    "material",
    "weight_kg_per_m",
    "source",
    "status",
}
_INSPECTION_RESULT_FIELDS = {
    "protocol_version",
    "operation",
    "input_contract_version",
    "source_format",
    "sheet_name",
    "header_row",
    "part_count",
    "component_count",
    "warnings",
    "ignored_sheets",
}
_ERROR_FIELDS = {"protocol_version", "operation", "failure"}
_FAILURE_FIELDS = {
    "code",
    "message",
    "action",
    "contract_version",
    "issues",
    "sheets",
    "meta",
}
_ISSUE_FIELDS = {"sheet", "row", "column", "field", "value", "reason"}
_SOURCE_FORMATS = {
    "standard_workbook",
    "legacy_workbook",
    "initial_workbook",
    "legacy_initial_workbook",
    "delimited_tekla_text",
    "fixed_width_tekla_text",
}
_SUMMARY_FIELDS = {
    "info_count",
    "warning_count",
    "severe_warning_count",
    "category_counts",
    "representative_messages",
}
_STAGE_HEARTBEAT_INTERVAL_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ExcelFinalProcessResult:
    protocol_version: int
    output_path: Path
    quality_status: str
    warning_count: int
    severe_warning_count: int
    report_summary: dict[str, object]
    internal_output_path: Path | None = None

    def quality_expectation(self) -> dict[str, int | str]:
        return {
            "quality_status": self.quality_status,
            "warning_count": self.warning_count,
            "severe_warning_count": self.severe_warning_count,
        }


@dataclass(frozen=True, slots=True)
class ExcelStage2ProcessResult:
    protocol_version: int
    output_path: Path
    internal_output_path: Path
    status: str
    matched_occurrence_count: int
    missing_drawing_count: int
    unmatched_drawing_count: int
    manual_occurrence_count: int
    quality_status: str
    warning_count: int
    severe_warning_count: int
    report_summary: dict[str, object]


@dataclass(frozen=True, slots=True)
class ExcelStage3ProcessResult:
    """Stage3 classification pipeline result."""
    protocol_version: int
    classification_excel: str
    deepened_excel: str
    bh_box_count: int
    matched_count: int
    unmatched_count: int
    classified_dxf_count: int
    filled_count: int
    manual_count: int


@dataclass(frozen=True, slots=True)
class ExcelFinalLookupResult:
    protocol_version: int
    category: str
    normalized_spec: str
    material: str | None
    weight_kg_per_m: float | None
    source: str
    status: str


class ExcelFinalIntegrationError(RuntimeError):
    """Base error for the platform-to-Excel-Final process boundary."""


class ExcelFinalUnavailableError(ExcelFinalIntegrationError):
    """Raised when the standalone Stage or one of its dependencies is missing."""


class ExcelFinalProcessError(ExcelFinalIntegrationError):
    """Raised when the isolated Stage process exits unsuccessfully."""


class ExcelFinalInputError(ExcelFinalIntegrationError):
    """Raised when a production table fails the versioned Stage input contract."""

    def __init__(self, failure: ExcelInputFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


def excel_final_stage_file_available(stage_root: Path, source_name: str) -> bool:
    """Accept a development source module or its protected legacy bytecode peer."""
    source_path = stage_root / source_name
    bytecode_path = source_path.with_suffix(".pyc")
    return source_path.is_file() or bytecode_path.is_file()


def get_excel_final_stage_root() -> Path:
    """Resolve the tracked standalone Stage in source and container layouts."""
    integration_file = Path(__file__).resolve()
    candidates: list[Path] = []
    if settings.excel_final_stage_root is not None:
        candidates.append(settings.excel_final_stage_root.expanduser())
    candidates.extend(
        (
            integration_file.parents[4] / "Stages" / "excel_final",
            integration_file.parents[3] / "Stages" / "excel_final",
        )
    )

    checked: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in checked:
            continue
        checked.add(resolved)
        if all(
            excel_final_stage_file_available(resolved, filename)
            for filename in _REQUIRED_STAGE_FILES
        ):
            return resolved

    raise ExcelFinalUnavailableError("Excel Final Stage is unavailable in configured locations")


def excel_final_dependencies_available() -> bool:
    """Return whether all third-party modules required by the Stage are installed."""
    return all(
        importlib.util.find_spec(module_name) is not None
        for module_name in ("numpy", "openpyxl", "pandas", "pymysql", "xlrd")
    )


def handbook_database_available() -> bool:
    """Probe the handbook database without importing Stage algorithm modules."""
    connection = None
    try:
        connection = pymysql.connect(**settings.handbook_database_config)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() == (1,)
    except pymysql.MySQLError as exc:
        logger.warning(
            "Hardware handbook database health check failed (error_type=%s)",
            exc.__class__.__name__,
        )
        return False
    finally:
        if connection is not None:
            connection.close()


def run_excel_final_pipeline(
    source_path: Path,
    output_path: Path,
) -> ExcelFinalProcessResult:
    """Run the canonical Stage and validate its versioned process result."""
    if not source_path.is_file():
        raise ExcelFinalProcessError("Excel Final source file does not exist")
    if output_path.suffix.lower() != ".xlsx":
        raise ValueError("Excel Final output must use the .xlsx extension")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    internal_output_path = output_path.with_name(
        f".{output_path.stem}.internal.xlsx"
    )
    publish_token = uuid.uuid4().hex
    stage_output_path = output_path.with_name(
        f".{output_path.stem}.{publish_token}.xlsx"
    )
    stage_internal_output_path = output_path.with_name(
        f".{output_path.stem}.{publish_token}.internal.xlsx"
    )
    try:
        completed = _run_stage(
            "process",
            "--input",
            str(source_path.resolve()),
            "--output",
            str(stage_output_path),
            "--internal-output",
            str(stage_internal_output_path),
        )
        _raise_for_failed_stage(completed, operation="process")
        payload = _result_payload(completed, operation="process")
        result = _process_result(payload, expected_output=stage_output_path)
        if not stage_output_path.is_file():
            raise ExcelFinalProcessError(
                "Excel Final Stage exited successfully without an output file"
            )
        if not stage_internal_output_path.is_file():
            raise ExcelFinalProcessError(
                "Excel Final Stage exited successfully without its internal import file"
            )
        stage_internal_output_path.replace(internal_output_path)
        stage_output_path.replace(output_path)
    finally:
        stage_output_path.unlink(missing_ok=True)
        stage_internal_output_path.unlink(missing_ok=True)
    return ExcelFinalProcessResult(
        protocol_version=result.protocol_version,
        output_path=output_path,
        quality_status=result.quality_status,
        warning_count=result.warning_count,
        severe_warning_count=result.severe_warning_count,
        report_summary=result.report_summary,
        internal_output_path=internal_output_path,
    )


def run_excel_stage2_pipeline(
    formal_stage1_path: Path,
    measurements_path: Path,
    output_path: Path,
    *,
    box_measurements_path: Path | None = None,
    on_heartbeat: Callable[[], None] | None = None,
) -> ExcelStage2ProcessResult:
    """Run the isolated BH/BOX Stage 2 and publish both validated workbooks."""
    if not formal_stage1_path.is_file():
        raise ExcelFinalProcessError("Excel Stage 1 formal workbook does not exist")
    if not measurements_path.is_file():
        raise ExcelFinalProcessError("BH measurement contract does not exist")
    if output_path.suffix.lower() != ".xlsx":
        raise ValueError("Excel Stage 2 output must use the .xlsx extension")

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    internal_output_path = output_path.with_name(
        f".{output_path.stem}.internal.xlsx"
    )
    publish_token = uuid.uuid4().hex
    stage_output_path = output_path.with_name(
        f".{output_path.stem}.{publish_token}.xlsx"
    )
    stage_internal_output_path = output_path.with_name(
        f".{output_path.stem}.{publish_token}.internal.xlsx"
    )
    try:
        arguments = [
            "process-stage2",
            "--stage1",
            str(formal_stage1_path.resolve()),
            "--measurements",
            str(measurements_path.resolve()),
        ]
        if box_measurements_path is not None:
            if not box_measurements_path.is_file():
                raise ExcelFinalProcessError("BOX measurement contract does not exist")
            arguments.extend((
                "--box-measurements",
                str(box_measurements_path.resolve()),
            ))
        arguments.extend((
            "--output",
            str(stage_output_path),
            "--internal-output",
            str(stage_internal_output_path),
        ))
        arguments = tuple(arguments)
        completed = (
            _run_stage(*arguments, on_heartbeat=on_heartbeat)
            if on_heartbeat is not None
            else _run_stage(*arguments)
        )
        _raise_for_failed_stage(completed, operation="process-stage2")
        payload = _result_payload(completed, operation="process-stage2")
        result = _stage2_process_result(
            payload,
            expected_output=stage_output_path,
            internal_output=internal_output_path,
        )
        if not stage_output_path.is_file():
            raise ExcelFinalProcessError(
                "Excel Stage 2 exited successfully without an output file"
            )
        if not stage_internal_output_path.is_file():
            raise ExcelFinalProcessError(
                "Excel Stage 2 exited successfully without its internal import file"
            )
        stage_internal_output_path.replace(internal_output_path)
        stage_output_path.replace(output_path)
    finally:
        stage_output_path.unlink(missing_ok=True)
        stage_internal_output_path.unlink(missing_ok=True)
    return replace(result, output_path=output_path)


# ---------------------------------------------------------------------------
# Excel Stage 3 — 异孔折判断对接
# ---------------------------------------------------------------------------

_STAGE3_REQUIRED_FILES = (
    "pyproject.toml",
    "src/excel_stage3/__init__.py",
    "src/excel_stage3/__main__.py",
    "src/excel_stage3/stage3.py",
)

_STAGE3_RESULT_FIELDS = {
    "protocol_version",
    "operation",
    "classification_excel",
    "deepened_excel",
    "bh_box_count",
    "matched_count",
    "unmatched_count",
    "classified_dxf_count",
    "filled_count",
    "manual_count",
}


def get_excel_stage3_root() -> Path:
    """Resolve the excel_stage3 Stage package directory."""
    integration_file = Path(__file__).resolve()
    candidates: list[Path] = []
    if settings.excel_stage3_root is not None:
        candidates.append(settings.excel_stage3_root.expanduser())
    candidates.extend((
        integration_file.parents[4] / "Stages" / "excel_stage3",
        integration_file.parents[3] / "Stages" / "excel_stage3",
    ))
    checked: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in checked:
            continue
        checked.add(resolved)
        if all((resolved / f).exists() for f in _STAGE3_REQUIRED_FILES):
            return resolved
    raise ExcelFinalUnavailableError("Excel Stage3 package is unavailable")


def run_excel_stage3_pipeline(
    stage2_excel_path: Path,
    dxf_dir: Path,
    output_dir: Path,
    *,
    encoding: str = "utf-8",
) -> ExcelStage3ProcessResult:
    """Run the excel_stage3 classification pipeline.

    Calls ``uv run excel-stage3`` in the Stage package directory and parses
    the versioned JSON result from stdout.
    """
    if not stage2_excel_path.is_file():
        raise ExcelFinalProcessError("Stage2 Excel file does not exist")
    if not dxf_dir.is_dir():
        raise ExcelFinalProcessError("DXF directory does not exist")

    stage_root = get_excel_stage3_root()
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "uv", "run", "--directory", str(stage_root),
        "excel-stage3",
        "--stage2-excel", str(stage2_excel_path.resolve()),
        "--dxf-dir", str(dxf_dir.resolve()),
        "--output-dir", str(output_dir.resolve()),
        "--encoding", encoding,
    ]

    logger.info("Running excel-stage3: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )

    if result.returncode != 0:
        stderr_tail = (result.stderr or "").splitlines()[-10:]
        raise ExcelFinalProcessError(
            f"Excel Stage3 failed (rc={result.returncode}): "
            + "\n".join(stderr_tail)
        )

    # Parse the JSON result from stdout
    result_lines = [
        line for line in (result.stdout or "").splitlines()
        if line.startswith(_RESULT_PREFIX)
    ]
    if len(result_lines) != 1:
        raise ExcelFinalProcessError(
            "Excel Stage3 returned an invalid result"
        )

    try:
        payload = json.loads(result_lines[0].removeprefix(_RESULT_PREFIX))
    except json.JSONDecodeError as exc:
        raise ExcelFinalProcessError(
            "Excel Stage3 returned invalid JSON"
        ) from exc

    if not isinstance(payload, dict) or set(payload) != _STAGE3_RESULT_FIELDS:
        raise ExcelFinalProcessError("Excel Stage3 result fields mismatch")
    if payload["protocol_version"] != _PROTOCOL_VERSION:
        raise ExcelFinalProcessError("Excel Stage3 unsupported protocol version")
    if payload["operation"] != "process-stage3":
        raise ExcelFinalProcessError("Excel Stage3 wrong operation")

    return ExcelStage3ProcessResult(
        protocol_version=_PROTOCOL_VERSION,
        classification_excel=str(payload["classification_excel"]),
        deepened_excel=str(payload["deepened_excel"]),
        bh_box_count=int(payload["bh_box_count"]),
        matched_count=int(payload["matched_count"]),
        unmatched_count=int(payload["unmatched_count"]),
        classified_dxf_count=int(payload["classified_dxf_count"]),
        filled_count=int(payload["filled_count"]),
        manual_count=int(payload["manual_count"]),
    )


def lookup_excel_final_weight(
    *,
    category: str,
    spec: str,
    material: str | None = None,
) -> ExcelFinalLookupResult:
    """Perform one category-aware handbook lookup through the isolated Stage."""
    category, normalized_spec, normalized_material = _normalize_lookup_request(
        category,
        spec,
        material,
    )
    arguments = ["lookup", "--category", category, "--spec", normalized_spec]
    if normalized_material is not None:
        arguments.extend(("--material", normalized_material))
    completed = _run_stage(*arguments)
    _raise_for_failed_stage(completed, operation="lookup")
    payload = _result_payload(completed, operation="lookup")
    return _lookup_result(
        payload,
        expected_category=category,
        expected_spec=normalized_spec,
        expected_material=normalized_material,
    )


def inspect_excel_stage1_path(source_path: Path) -> ExcelStage1Inspection:
    """Run the Stage-owned, side-effect-free source inspection."""
    completed = _run_stage("inspect", "--input", str(source_path.resolve()))
    _raise_for_failed_stage(completed, operation="inspect")
    payload = _result_payload(completed, operation="inspect")
    return _inspection_result(payload)


def inspect_excel_stage1_bytes(
    *,
    file_name: str,
    payload: bytes,
    expected_sha256: str | None = None,
) -> ExcelStage1Inspection:
    """Inspect verified object bytes without exposing storage concerns to the Stage."""
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and actual_sha256 != expected_sha256:
        raise ExcelFinalInputError(
            ExcelInputFailure(
                code="EXCEL_INPUT_OBJECT_CHANGED",
                message="Excel 文件内容已发生变化。",
                action="请重新上传文件并重新冻结输入后再运行。",
                contract_version=_INPUT_CONTRACT_VERSION,
                issues=(),
                sheets=(),
                meta={
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                    "issue_count": 0,
                    "issues_truncated": False,
                    "sheet_count": 0,
                    "sheets_truncated": False,
                },
            )
        )
    suffix = Path(file_name).suffix.lower()
    with tempfile.TemporaryDirectory(prefix="excel-stage1-inspect-") as directory:
        source_path = Path(directory) / f"source{suffix}"
        source_path.write_bytes(payload)
        return inspect_excel_stage1_path(source_path)


def _normalize_lookup_request(
    category: str,
    spec: str,
    material: str | None,
) -> tuple[str, str, str | None]:
    normalized_category = str(category or "").strip()
    normalized_spec = str(spec or "").strip()
    normalized_material = (
        str(material or "").replace(" ", "").replace("　", "").upper() or None
    )
    if normalized_category not in _LOOKUP_CATEGORIES:
        raise ValueError(f"Unsupported handbook category: {normalized_category}")
    if not normalized_spec:
        raise ValueError("Handbook spec is required")

    compact_spec = normalized_spec.replace(" ", "").replace("　", "")
    compact_spec = re.sub(r"(?<=\d)[X×](?=\d)", "*", compact_spec).upper()
    circular_match = _CIRCULAR_HOLLOW_RE.fullmatch(compact_spec)
    if circular_match is not None:
        prefix, outer_text, thickness_text = circular_match.groups()
        expected_category = "steel_pipe" if prefix == "PIP" else "square_tube"
        if normalized_category != expected_category:
            raise ValueError(f"{prefix} spec requires {expected_category} category")
        outer = Decimal(outer_text)
        thickness = Decimal(thickness_text)
        if outer <= 0 or thickness <= 0 or outer <= thickness * 2:
            raise ValueError("PIP/PD spec requires D>0, t>0, and D>2t")
        normalized_spec = compact_spec

    material_family = next(
        (
            prefix
            for prefix in _D_MATERIAL_CATEGORY_BY_PREFIX
            if normalized_material is not None
            and normalized_material.startswith(prefix)
        ),
        None,
    )
    material_category = (
        _D_MATERIAL_CATEGORY_BY_PREFIX.get(material_family)
        if material_family is not None
        else None
    )
    d_match = re.fullmatch(r"D(\d+(?:\.\d+)?)", normalized_spec, flags=re.IGNORECASE)
    if d_match:
        if normalized_material is None:
            raise ValueError("D-series handbook lookup requires material")
        normalized_spec = d_match.group(1)
        if material_category is None:
            raise ValueError("Unsupported D-series material")
        if normalized_category != material_category:
            raise ValueError(
                f"D-series {material_family} material requires "
                f"{material_category} category"
            )

    if normalized_category in {"round_bar", "rebar"} and normalized_material is None:
        raise ValueError(f"{normalized_category} handbook lookup requires material")
    if normalized_category == "round_bar" and normalized_material is not None:
        if material_category != "round_bar":
            raise ValueError("round_bar category conflicts with material")
    if normalized_category == "rebar" and normalized_material is not None:
        if material_category != "rebar":
            raise ValueError("rebar category conflicts with material")
    return normalized_category, normalized_spec, normalized_material


def _result_payload(
    completed: subprocess.CompletedProcess[str],
    *,
    operation: Literal["process", "process-stage2", "lookup", "inspect"],
) -> dict[str, object]:
    result_lines = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith(_RESULT_PREFIX)
    ]
    if len(result_lines) != 1:
        raise ExcelFinalProcessError(f"Excel Final returned an invalid {operation} result")
    try:
        payload = json.loads(result_lines[0].removeprefix(_RESULT_PREFIX))
    except json.JSONDecodeError as exc:
        raise ExcelFinalProcessError(
            f"Excel Final returned an invalid {operation} result"
        ) from exc
    if not isinstance(payload, dict):
        raise ExcelFinalProcessError(f"Excel Final returned an invalid {operation} result")
    return payload


def _inspection_result(payload: dict[str, object]) -> ExcelStage1Inspection:
    try:
        if set(payload) != _INSPECTION_RESULT_FIELDS:
            raise ValueError("invalid fields")
        if payload["protocol_version"] != _PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        if payload["operation"] != "inspect":
            raise ValueError("wrong operation")
        if payload["input_contract_version"] != _INPUT_CONTRACT_VERSION:
            raise ValueError("unsupported input contract version")
        source_format = payload["source_format"]
        sheet_name = payload["sheet_name"]
        if not isinstance(source_format, str) or source_format not in _SOURCE_FORMATS:
            raise ValueError("invalid source format")
        if sheet_name is not None and (
            not isinstance(sheet_name, str) or not sheet_name or len(sheet_name) > 128
        ):
            raise ValueError("invalid sheet name")
        header_row = _positive_int(payload["header_row"])
        part_count = _non_negative_int(payload["part_count"])
        component_count = _non_negative_int(payload["component_count"])
        warnings = _bounded_text_list(payload["warnings"], maximum_items=10, maximum_text=500)
        ignored_sheets = _bounded_text_list(
            payload["ignored_sheets"], maximum_items=10, maximum_text=128
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExcelFinalProcessError(
            "Excel Final returned an invalid inspect result"
        ) from exc
    return ExcelStage1Inspection(
        protocol_version=_PROTOCOL_VERSION,
        input_contract_version=_INPUT_CONTRACT_VERSION,
        source_format=source_format,
        sheet_name=sheet_name,
        header_row=header_row,
        part_count=part_count,
        component_count=component_count,
        warnings=warnings,
        ignored_sheets=ignored_sheets,
    )


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected a non-negative integer")
    return value


def _positive_int(value: object) -> int:
    result = _non_negative_int(value)
    if result == 0:
        raise ValueError("expected a positive integer")
    return result


def _bounded_text_list(
    value: object,
    *,
    maximum_items: int,
    maximum_text: int,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError("invalid bounded text list")
    return tuple(_bounded_text(item, maximum=maximum_text) for item in value)


def _validated_summary(
    value: object,
    *,
    warning_count: int,
    severe_warning_count: int,
) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != _SUMMARY_FIELDS:
        raise ValueError("invalid report summary shape")
    info_count = _non_negative_int(value["info_count"])
    if _non_negative_int(value["warning_count"]) != warning_count:
        raise ValueError("warning count mismatch")
    if _non_negative_int(value["severe_warning_count"]) != severe_warning_count:
        raise ValueError("severe warning count mismatch")

    raw_categories = value["category_counts"]
    if not isinstance(raw_categories, Mapping) or len(raw_categories) > 50:
        raise ValueError("invalid category counts")
    categories: dict[str, int] = {}
    for category, count in raw_categories.items():
        if not isinstance(category, str) or not category or len(category) > 128:
            raise ValueError("invalid report category")
        categories[category] = _non_negative_int(count)
    if sum(categories.values()) != info_count + warning_count + severe_warning_count:
        raise ValueError("report detail count mismatch")

    raw_messages = value["representative_messages"]
    if not isinstance(raw_messages, list) or len(raw_messages) > 10:
        raise ValueError("invalid representative messages")
    messages: list[str] = []
    for message in raw_messages:
        if not isinstance(message, str) or len(message) > 500:
            raise ValueError("invalid representative message")
        messages.append(message)
    return {
        "info_count": info_count,
        "warning_count": warning_count,
        "severe_warning_count": severe_warning_count,
        "category_counts": categories,
        "representative_messages": messages,
    }


def _validate_quality_status(status: object, warning_count: int, severe_count: int) -> str:
    if not isinstance(status, str) or status not in _QUALITY_STATUSES:
        raise ValueError("unknown quality status")
    if status == "ok" and (warning_count != 0 or severe_count != 0):
        raise ValueError("ok status has non-zero counts")
    if status == "warning" and (warning_count == 0 or severe_count != 0):
        raise ValueError("warning status has impossible counts")
    if status == "severe_warning" and severe_count == 0:
        raise ValueError("severe status has no severe details")
    return status


def _process_result(
    payload: dict[str, object],
    *,
    expected_output: Path,
) -> ExcelFinalProcessResult:
    try:
        if set(payload) != _PROCESS_RESULT_FIELDS:
            raise ValueError("invalid fields")
        if payload["protocol_version"] != _PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        if payload["operation"] != "process":
            raise ValueError("wrong operation")
        raw_output = payload["output_path"]
        if not isinstance(raw_output, str) or not raw_output:
            raise ValueError("invalid output path")
        output_path = Path(raw_output).resolve()
        if output_path != expected_output.resolve():
            raise ValueError("unexpected output path")
        warning_count = _non_negative_int(payload["warning_count"])
        severe_count = _non_negative_int(payload["severe_warning_count"])
        quality_status = _validate_quality_status(
            payload["quality_status"],
            warning_count,
            severe_count,
        )
        summary = _validated_summary(
            payload["report_summary"],
            warning_count=warning_count,
            severe_warning_count=severe_count,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ExcelFinalProcessError(
            "Excel Final returned an invalid process result"
        ) from exc
    return ExcelFinalProcessResult(
        protocol_version=_PROTOCOL_VERSION,
        output_path=output_path,
        quality_status=quality_status,
        warning_count=warning_count,
        severe_warning_count=severe_count,
        report_summary=summary,
    )


def _stage2_process_result(
    payload: dict[str, object],
    *,
    expected_output: Path,
    internal_output: Path,
) -> ExcelStage2ProcessResult:
    try:
        if set(payload) != _PROCESS_STAGE2_RESULT_FIELDS:
            raise ValueError("invalid fields")
        stage2_status = payload["status"]
        if stage2_status not in {"complete", "partial", "noop"}:
            raise ValueError("invalid Stage 2 status")
        counts = {
            field: _non_negative_int(payload[field])
            for field in (
                "matched_occurrence_count",
                "missing_drawing_count",
                "unmatched_drawing_count",
                "manual_occurrence_count",
            )
        }
        problem_count = (
            counts["missing_drawing_count"]
            + counts["unmatched_drawing_count"]
            + counts["manual_occurrence_count"]
        )
        if counts["manual_occurrence_count"] > counts["matched_occurrence_count"]:
            raise ValueError("manual occurrences exceed matched occurrences")
        if stage2_status == "noop" and any(counts.values()):
            raise ValueError("noop Stage 2 has non-zero counts")
        if stage2_status == "complete" and problem_count:
            raise ValueError("complete Stage 2 has unresolved counts")
        if stage2_status == "partial" and problem_count == 0:
            raise ValueError("partial Stage 2 has no unresolved counts")
        base_payload = {
            field: payload[field]
            for field in _PROCESS_RESULT_FIELDS
        }
        base_payload["operation"] = "process"
        base = _process_result(base_payload, expected_output=expected_output)
    except (KeyError, TypeError, ValueError) as exc:
        raise ExcelFinalProcessError(
            "Excel Final returned an invalid process-stage2 result"
        ) from exc
    return ExcelStage2ProcessResult(
        protocol_version=base.protocol_version,
        output_path=base.output_path,
        internal_output_path=internal_output.resolve(),
        status=stage2_status,
        matched_occurrence_count=counts["matched_occurrence_count"],
        missing_drawing_count=counts["missing_drawing_count"],
        unmatched_drawing_count=counts["unmatched_drawing_count"],
        manual_occurrence_count=counts["manual_occurrence_count"],
        quality_status=base.quality_status,
        warning_count=base.warning_count,
        severe_warning_count=base.severe_warning_count,
        report_summary=base.report_summary,
    )


def _lookup_result(
    payload: dict[str, object],
    *,
    expected_category: str,
    expected_spec: str,
    expected_material: str | None,
) -> ExcelFinalLookupResult:
    try:
        if set(payload) != _LOOKUP_RESULT_FIELDS:
            raise ValueError("invalid fields")
        if payload["protocol_version"] != _PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")
        if payload["operation"] != "lookup":
            raise ValueError("wrong operation")
        category = payload["category"]
        spec = payload["normalized_spec"]
        material = payload["material"]
        source = payload["source"]
        status = payload["status"]
        if category != expected_category or spec != expected_spec or material != expected_material:
            raise ValueError("lookup identity mismatch")
        if not isinstance(source, str) or not source or len(source) > 255:
            raise ValueError("invalid lookup source")
        if not isinstance(status, str) or status not in _LOOKUP_STATUSES:
            raise ValueError("invalid lookup status")
        raw_weight = payload["weight_kg_per_m"]
        if raw_weight is not None and (
            isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float))
        ):
            raise ValueError("invalid lookup weight")
        weight = float(raw_weight) if raw_weight is not None else None
        if (status == "hit") != (weight is not None):
            raise ValueError("lookup status and weight mismatch")
        if weight is not None and (weight <= 0 or weight > 2000):
            raise ValueError("lookup weight out of range")
    except (KeyError, TypeError, ValueError) as exc:
        raise ExcelFinalProcessError(
            "Excel Final returned an invalid lookup result"
        ) from exc
    return ExcelFinalLookupResult(
        protocol_version=_PROTOCOL_VERSION,
        category=expected_category,
        normalized_spec=expected_spec,
        material=expected_material,
        weight_kg_per_m=weight,
        source=source,
        status=status,
    )


def _run_stage(
    *arguments: str,
    on_heartbeat: Callable[[], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    stage_root = get_excel_final_stage_root()
    if not excel_final_dependencies_available():
        raise ExcelFinalUnavailableError("Excel Final Python dependencies are unavailable")
    timeout_seconds = (
        settings.excel_stage2_timeout_seconds
        if arguments and arguments[0] == "process-stage2"
        else settings.excel_final_timeout_seconds
    )

    command = [
        sys.executable,
        "-m",
        "app.modules.excel_processing.stage_runner",
        *arguments,
        "--stage-root",
        str(stage_root),
    ]
    if on_heartbeat is None:
        try:
            return subprocess.run(
                command,
                cwd=stage_root,
                env=_stage_environment(),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExcelFinalProcessError(
                f"Excel Final Stage exceeded {timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            raise ExcelFinalUnavailableError("Unable to start Excel Final Stage") from exc

    try:
        process = subprocess.Popen(
            command,
            cwd=stage_root,
            env=_stage_environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise ExcelFinalUnavailableError("Unable to start Excel Final Stage") from exc

    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            try:
                stdout, stderr = process.communicate(
                    timeout=min(_STAGE_HEARTBEAT_INTERVAL_SECONDS, remaining)
                )
                return subprocess.CompletedProcess(
                    command,
                    process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
            except subprocess.TimeoutExpired:
                on_heartbeat()
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ExcelFinalProcessError(
            f"Excel Final Stage exceeded {timeout_seconds} seconds"
        ) from exc
    except BaseException:
        process.kill()
        process.communicate()
        raise


def _stage_environment() -> dict[str, str]:
    environment = os.environ.copy()
    database_config = settings.handbook_database_config
    application_root = str(Path(__file__).resolve().parents[3])
    inherited_python_path = environment.get("PYTHONPATH", "")
    environment.update(
        {
            "DWG_HANDBOOK_MYSQL_HOST": str(database_config["host"]),
            "DWG_HANDBOOK_MYSQL_PORT": str(database_config["port"]),
            "DWG_HANDBOOK_MYSQL_DATABASE": str(database_config["database"]),
            "DWG_HANDBOOK_MYSQL_USER": str(database_config["user"]),
            "DWG_HANDBOOK_MYSQL_PASSWORD": str(database_config["password"]),
            # The child runs with cwd=Stages/excel_final. Make the source
            # backend root (or Docker /app root) explicit instead of relying
            # on the parent console script's transient sys.path.
            "PYTHONPATH": os.pathsep.join(
                value for value in (application_root, inherited_python_path) if value
            ),
        }
    )
    return environment


def _raise_for_failed_stage(
    completed: subprocess.CompletedProcess[str],
    *,
    operation: Literal["process", "process-stage2", "lookup", "inspect"],
) -> None:
    if completed.returncode == 0:
        return
    error_lines = [
        line
        for line in completed.stdout.splitlines()
        if line.startswith(_ERROR_PREFIX)
    ]
    if error_lines:
        if len(error_lines) != 1:
            raise ExcelFinalProcessError(
                f"Excel Final returned an invalid {operation} error"
            )
        try:
            payload = json.loads(error_lines[0].removeprefix(_ERROR_PREFIX))
            failure = _input_failure_from_payload(payload, operation=operation)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ExcelFinalProcessError(
                f"Excel Final returned an invalid {operation} error"
            ) from exc
        raise ExcelFinalInputError(failure)
    details = (completed.stderr or completed.stdout or "unknown error").strip()
    logger.error(
        "Excel Final %s failed (rc=%s): %s",
        operation,
        completed.returncode,
        details[-4000:],
    )
    failure_kind = "input_parse" if any(
        marker in details
        for marker in (
            "ParserError",
            "BadZipFile",
            "Excel file format cannot be determined",
            "Unsupported format",
        )
    ) else "processing"
    logger.error(
        "Excel Final Stage exited with status %s (%s failure)",
        completed.returncode,
        failure_kind,
    )
    if any(
        marker in details
        for marker in (
            "ParserError",
            "BadZipFile",
            "Excel file format cannot be determined",
            "Unsupported format",
        )
    ):
        message = "The input file could not be parsed as a supported Excel Final format."
    else:
        message = "Excel Final Stage failed while processing the input."
    raise ExcelFinalProcessError(message)


def _input_failure_from_payload(
    payload: object,
    *,
    operation: str,
) -> ExcelInputFailure:
    if not isinstance(payload, Mapping) or set(payload) != _ERROR_FIELDS:
        raise ValueError("invalid error shape")
    if payload["protocol_version"] != _PROTOCOL_VERSION:
        raise ValueError("unsupported protocol version")
    if payload["operation"] != operation:
        raise ValueError("wrong operation")
    raw_failure = payload["failure"]
    if not isinstance(raw_failure, Mapping) or set(raw_failure) != _FAILURE_FIELDS:
        raise ValueError("invalid failure shape")

    code = _bounded_text(raw_failure["code"], maximum=64)
    if re.fullmatch(r"EXCEL_INPUT_[A-Z0-9_]+", code) is None:
        raise ValueError("invalid input failure code")
    message = _bounded_text(raw_failure["message"], maximum=500)
    action = _bounded_text(raw_failure["action"], maximum=1000)
    if raw_failure["contract_version"] != _INPUT_CONTRACT_VERSION:
        raise ValueError("unsupported input contract version")

    raw_issues = raw_failure["issues"]
    if not isinstance(raw_issues, list) or len(raw_issues) > 20:
        raise ValueError("invalid issues")
    issues = tuple(_input_issue_from_payload(issue) for issue in raw_issues)

    raw_sheets = raw_failure["sheets"]
    if not isinstance(raw_sheets, list) or len(raw_sheets) > 10:
        raise ValueError("invalid sheets")
    sheets = tuple(_bounded_text(sheet, maximum=128) for sheet in raw_sheets)

    raw_meta = raw_failure["meta"]
    if not isinstance(raw_meta, Mapping) or len(raw_meta) > 30:
        raise ValueError("invalid metadata")
    meta = dict(raw_meta)
    encoded_meta = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
    if len(encoded_meta) > 8000:
        raise ValueError("metadata too large")
    return ExcelInputFailure(
        code=code,
        message=message,
        action=action,
        contract_version=_INPUT_CONTRACT_VERSION,
        issues=issues,
        sheets=sheets,
        meta=meta,
    )


def _input_issue_from_payload(payload: object) -> ExcelInputIssue:
    if not isinstance(payload, Mapping) or set(payload) != _ISSUE_FIELDS:
        raise ValueError("invalid issue shape")
    sheet = _optional_bounded_text(payload["sheet"], maximum=128)
    row = payload["row"]
    if row is not None:
        row = _positive_int(row)
    column = _optional_bounded_text(payload["column"], maximum=16)
    field = _optional_bounded_text(payload["field"], maximum=128)
    value = _optional_bounded_text(payload["value"], maximum=160)
    reason = _bounded_text(payload["reason"], maximum=128)
    return ExcelInputIssue(
        sheet=sheet,
        row=row,
        column=column,
        field=field,
        value=value,
        reason=reason,
    )


def _bounded_text(value: object, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("invalid text")
    return value


def _optional_bounded_text(value: object, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, maximum=maximum)
