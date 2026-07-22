from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymysql

from app.modules.excel_processing.schemas import HandbookCategory
from app.platform.config.settings import settings

logger = logging.getLogger(__name__)

SourceFormat = Literal["init", "canonical", "tsv"]
_RESULT_PREFIX = "DWG_EXCEL_FINAL_RESULT="
_PROTOCOL_VERSION = 1
_REQUIRED_STAGE_FILES = ("main.py", "pipeline.py", "handbook.py")
_QUALITY_STATUSES = {"ok", "warning", "severe_warning"}
_LOOKUP_STATUSES = {"hit", "not_found", "skipped"}
_LOOKUP_CATEGORIES = {item.value for item in HandbookCategory}
_PROCESS_RESULT_FIELDS = {
    "protocol_version",
    "operation",
    "output_path",
    "quality_status",
    "warning_count",
    "severe_warning_count",
    "report_summary",
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
_SUMMARY_FIELDS = {
    "info_count",
    "warning_count",
    "severe_warning_count",
    "category_counts",
    "representative_messages",
}


@dataclass(frozen=True, slots=True)
class ExcelFinalProcessResult:
    protocol_version: int
    output_path: Path
    quality_status: str
    warning_count: int
    severe_warning_count: int
    report_summary: dict[str, object]

    def quality_expectation(self) -> dict[str, int | str]:
        return {
            "quality_status": self.quality_status,
            "warning_count": self.warning_count,
            "severe_warning_count": self.severe_warning_count,
        }


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
        if all((resolved / filename).is_file() for filename in _REQUIRED_STAGE_FILES):
            return resolved

    raise ExcelFinalUnavailableError("Excel Final Stage is unavailable in configured locations")


def excel_final_dependencies_available() -> bool:
    """Return whether all third-party modules required by the Stage are installed."""
    return all(
        importlib.util.find_spec(module_name) is not None
        for module_name in ("numpy", "openpyxl", "pandas", "pymysql", "xlrd")
    )


def handbook_database_available() -> bool:
    """Probe the handbook database without importing the legacy Stage modules."""
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
    *,
    source_format: SourceFormat,
) -> ExcelFinalProcessResult:
    """Run the canonical Stage and validate its versioned process result."""
    if source_format not in ("init", "canonical", "tsv"):
        raise ValueError(f"Unsupported Excel Final source format: {source_format}")
    if not source_path.is_file():
        raise ExcelFinalProcessError("Excel Final source file does not exist")
    if output_path.suffix.lower() != ".xlsx":
        raise ValueError("Excel Final output must use the .xlsx extension")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    completed = _run_stage(
        "process",
        "--format",
        source_format,
        "--input",
        str(source_path.resolve()),
        "--output",
        str(output_path.resolve()),
    )
    _raise_for_failed_stage(completed)
    payload = _result_payload(completed, operation="process")
    result = _process_result(payload, expected_output=output_path)
    if not output_path.is_file():
        raise ExcelFinalProcessError("Excel Final Stage exited successfully without an output file")
    return result


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
    _raise_for_failed_stage(completed)
    payload = _result_payload(completed, operation="lookup")
    return _lookup_result(
        payload,
        expected_category=category,
        expected_spec=normalized_spec,
        expected_material=normalized_material,
    )


def _normalize_lookup_request(
    category: str,
    spec: str,
    material: str | None,
) -> tuple[str, str, str | None]:
    normalized_category = str(category or "").strip()
    normalized_spec = str(spec or "").strip()
    normalized_material = str(material or "").replace(" ", "").upper() or None
    if normalized_category not in _LOOKUP_CATEGORIES:
        raise ValueError(f"Unsupported handbook category: {normalized_category}")
    if not normalized_spec:
        raise ValueError("Handbook spec is required")

    d_match = re.fullmatch(r"D(\d+(?:\.\d+)?)", normalized_spec, flags=re.IGNORECASE)
    if d_match:
        if normalized_material is None:
            raise ValueError("D-series handbook lookup requires material")
        normalized_spec = d_match.group(1)
        if normalized_material.startswith("HRB") and normalized_category != "rebar":
            raise ValueError("D-series HRB material requires rebar category")
        if normalized_material.startswith(("HPB", "Q355B")) and normalized_category != "round_bar":
            raise ValueError("D-series HPB/Q355B material requires round_bar category")
        if not normalized_material.startswith(("HRB", "HPB", "Q355B")):
            raise ValueError("Unsupported D-series material")

    if normalized_category in {"round_bar", "rebar"} and normalized_material is None:
        raise ValueError(f"{normalized_category} handbook lookup requires material")
    if normalized_category == "round_bar" and normalized_material is not None:
        if not normalized_material.startswith(("HPB", "Q355B")):
            raise ValueError("round_bar category conflicts with material")
    if normalized_category == "rebar" and normalized_material is not None:
        if not normalized_material.startswith("HRB"):
            raise ValueError("rebar category conflicts with material")
    return normalized_category, normalized_spec, normalized_material


def _result_payload(
    completed: subprocess.CompletedProcess[str],
    *,
    operation: Literal["process", "lookup"],
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


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected a non-negative integer")
    return value


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


def _run_stage(*arguments: str) -> subprocess.CompletedProcess[str]:
    stage_root = get_excel_final_stage_root()
    if not excel_final_dependencies_available():
        raise ExcelFinalUnavailableError("Excel Final Python dependencies are unavailable")

    command = [
        sys.executable,
        "-m",
        "app.modules.excel_processing.stage_runner",
        *arguments,
        "--stage-root",
        str(stage_root),
    ]
    try:
        return subprocess.run(
            command,
            cwd=stage_root,
            env=_stage_environment(),
            capture_output=True,
            text=True,
            timeout=settings.excel_final_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExcelFinalProcessError(
            f"Excel Final Stage exceeded {settings.excel_final_timeout_seconds} seconds"
        ) from exc
    except OSError as exc:
        raise ExcelFinalUnavailableError("Unable to start Excel Final Stage") from exc


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


def _raise_for_failed_stage(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.returncode == 0:
        return
    details = (completed.stderr or completed.stdout or "unknown error").strip()
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
