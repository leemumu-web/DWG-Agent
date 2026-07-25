"""Versioned Steel DXF Split 1.5.2 process and naming contract."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any

from app.platform.config.settings import settings

SPLITTER_VERSION = "1.5.2"
CLI_SCHEMA = "DWG-AGENT-STEEL-DXF-SPLIT-CLI-1.0"
CLASSIFIED_INPUT_SCHEMA = "STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0"
VALIDATION_SCHEMA = "DWG-AGENT-DXF-SPLIT-VALIDATION-1.0"
MANIFEST_SCHEMA = "DWG-AGENT-DXF-SPLIT-MANIFEST-1.0"
BH_SOURCE_CONTRACT = "project_tekla_bh_dxf_v1"
BOX_SOURCE_CONTRACT = "project_tekla_box_dxf_v1"
BH_PROJECT_LEDGER_FILENAME = "BH拆板信息表.xlsx"
SUPPORTED_PART_TYPES = frozenset({"BH", "BOX"})

ERROR_CODE_SPLIT_FAILED = "DXF_SPLIT_FAILED"
MAX_AUTOMATIC_ATTEMPTS = 3


class DxfSplitError(RuntimeError):
    """Technical split failure whose message is safe for the Job ledger."""


def source_contract_for(part_type: str) -> str | None:
    if part_type == "BH":
        return BH_SOURCE_CONTRACT
    if part_type == "BOX":
        return BOX_SOURCE_CONTRACT
    return None


def member_name(source_name: str) -> str:
    name = Path(source_name).name
    if Path(name).suffix.casefold() != ".dxf":
        raise DxfSplitError(f"拆板输入不是 DXF: {name}")
    stem = Path(name).stem
    if stem.endswith("_拆板前"):
        stem = stem.removesuffix("_拆板前")
    elif stem.endswith("拆板前"):
        stem = stem.removesuffix("拆板前").rstrip("_- ")
    if not stem:
        raise DxfSplitError("拆板输入的构件名称为空。")
    return stem


def invoke_splitter(
    input_directory: Path,
    output_directory: Path,
    *,
    classification_manifest: Path,
    expected_input_count: int,
    progress_callback: Callable[[int, int, int, int, int], None] | None = None,
) -> dict[str, Any]:
    """Run the immutable Stage CLI and wrap its legacy JSON list in a platform schema."""
    if expected_input_count <= 0:
        raise DxfSplitError("拆板 CLI 不能接收空的自动处理批次。")
    classification_manifest = Path(classification_manifest)
    if classification_manifest.is_symlink() or not classification_manifest.is_file():
        raise DxfSplitError("拆板分类清单不可用。")
    classification_manifest = classification_manifest.resolve()
    progress_path = output_directory / ".dwg-agent-split-progress.json"
    command = [
        sys.executable,
        "-m",
        "steel_dxf_split.cli",
        str(input_directory),
        "--output-dir",
        str(output_directory),
        "--classification-manifest",
        str(classification_manifest),
        "--authorize-tekla-bh-single-part-profile",
        BH_SOURCE_CONTRACT,
        "--authorize-tekla-box-single-part-profile",
        BOX_SOURCE_CONTRACT,
        "--progress-json",
        str(progress_path),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    last_processed = 0

    def consume_progress() -> None:
        nonlocal last_processed
        if not progress_path.exists():
            return
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DxfSplitError("DXF 拆板进度文件无效。") from exc
        if (
            not isinstance(progress, dict)
            or progress.get("schema") != "STEEL-DXF-SPLIT-PROGRESS-1"
            or progress.get("input_count") != expected_input_count
            or not all(
                isinstance(progress.get(field), int)
                for field in (
                    "processed_count",
                    "auto_accepted_count",
                    "manual_review_count",
                    "failed_count",
                )
            )
        ):
            raise DxfSplitError("DXF 拆板进度合同不匹配。")
        processed = int(progress["processed_count"])
        auto_count = int(progress["auto_accepted_count"])
        manual_count = int(progress["manual_review_count"])
        failed_count = int(progress["failed_count"])
        if processed < last_processed or processed > expected_input_count:
            raise DxfSplitError("DXF 拆板进度不是有效的单调计数。")
        if (
            min(auto_count, manual_count, failed_count) < 0
            or auto_count + manual_count + failed_count != processed
        ):
            raise DxfSplitError("DXF 拆板进度分类计数不一致。")
        if processed > last_processed:
            last_processed = processed
            if progress_callback is not None:
                progress_callback(
                    processed,
                    expected_input_count,
                    auto_count,
                    manual_count,
                    failed_count,
                )

    deadline = monotonic() + settings.dxf_split_timeout_seconds
    try:
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, settings.dxf_split_timeout_seconds)
            try:
                stdout, stderr = process.communicate(timeout=min(0.25, remaining))
                consume_progress()
                break
            except subprocess.TimeoutExpired:
                consume_progress()
    except BaseException as exc:
        process.kill()
        process.communicate()
        if isinstance(exc, subprocess.TimeoutExpired):
            raise DxfSplitError("DXF 拆板执行超时。") from exc
        raise
    completed = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )
    if completed.returncode not in {0, 1, 2}:
        message = completed.stderr.strip() or "DXF 拆板进程异常退出。"
        raise DxfSplitError(message)
    try:
        summaries = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        message = completed.stderr.strip() or "DXF 拆板 CLI 未返回合法 JSON。"
        raise DxfSplitError(message) from exc
    if not isinstance(summaries, list):
        raise DxfSplitError("DXF 拆板 CLI 顶层结果不是数组。")
    if completed.stderr.strip():
        raise DxfSplitError("DXF 拆板成功或待复核退出时产生了非预期 stderr。")
    if len(summaries) != expected_input_count or not all(
        isinstance(item, dict) for item in summaries
    ):
        raise DxfSplitError("DXF 拆板逐图结果与冻结输入数量不一致。")
    if any(item.get("compiler_version") != SPLITTER_VERSION for item in summaries):
        raise DxfSplitError("DXF 拆板 CLI 报告的算法版本不一致。")
    routes = [item.get("automation_route") for item in summaries]
    if any(route not in {"auto_accepted", "manual_review", "failed"} for route in routes):
        raise DxfSplitError("DXF 拆板 CLI 返回了未知业务路由。")
    manual_review_count = routes.count("manual_review")
    failed_count = routes.count("failed")
    if (
        (completed.returncode == 0 and (manual_review_count or failed_count))
        or (completed.returncode == 1 and (not manual_review_count or failed_count))
        or (completed.returncode == 2 and not failed_count)
    ):
        raise DxfSplitError("DXF 拆板 CLI 退出码与业务路由不一致。")
    return {
        "schema": CLI_SCHEMA,
        "splitter_version": SPLITTER_VERSION,
        "status": (
            "completed_with_review"
            if manual_review_count or failed_count
            else "completed"
        ),
        "exit_code": completed.returncode,
        "input_count": expected_input_count,
        "auto_accepted_count": routes.count("auto_accepted"),
        "manual_review_count": manual_review_count,
        "failed_count": failed_count,
        "source_contracts": {
            "BH": BH_SOURCE_CONTRACT,
            "BOX": BOX_SOURCE_CONTRACT,
        },
        "results": summaries,
    }


def publish_empty_bh_ledger(output_directory: Path) -> Path:
    """Use the version-pinned Stage writer for an empty, header-only formal ledger."""
    from steel_dxf_split.bh_project_ledger import write_bh_project_ledger

    path = write_bh_project_ledger((), output_directory)
    if path.name != BH_PROJECT_LEDGER_FILENAME or not path.is_file():
        raise DxfSplitError("空批次 BH 拆板信息表未按 1.5.2 契约生成。")
    return path
