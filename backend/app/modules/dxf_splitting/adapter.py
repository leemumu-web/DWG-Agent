"""Versioned Steel DXF Split 1.5.2 process and naming contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.platform.config.settings import settings

SPLITTER_VERSION = "1.5.2"
CLI_SCHEMA = "DWG-AGENT-STEEL-DXF-SPLIT-CLI-1.0"
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
    expected_input_count: int,
) -> dict[str, Any]:
    """Run the immutable Stage CLI and wrap its legacy JSON list in a platform schema."""
    if expected_input_count <= 0:
        raise DxfSplitError("拆板 CLI 不能接收空的自动处理批次。")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "steel_dxf_split.cli",
                str(input_directory),
                "--output-dir",
                str(output_directory),
                "--authorize-tekla-bh-single-part-profile",
                BH_SOURCE_CONTRACT,
                "--authorize-tekla-box-single-part-profile",
                BOX_SOURCE_CONTRACT,
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=settings.dxf_split_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise DxfSplitError("DXF 拆板执行超时。") from exc
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
    if completed.returncode == 2:
        failures = [
            str(item.get("error"))
            for item in summaries
            if isinstance(item, dict) and item.get("error")
        ]
        message = "；".join(failures) or completed.stderr.strip() or "DXF 拆板执行失败。"
        raise DxfSplitError(message)
    if completed.stderr.strip():
        raise DxfSplitError("DXF 拆板成功或待复核退出时产生了非预期 stderr。")
    if len(summaries) != expected_input_count or not all(
        isinstance(item, dict) for item in summaries
    ):
        raise DxfSplitError("DXF 拆板逐图结果与冻结输入数量不一致。")
    if any(item.get("compiler_version") != SPLITTER_VERSION for item in summaries):
        raise DxfSplitError("DXF 拆板 CLI 报告的算法版本不一致。")
    routes = [item.get("automation_route") for item in summaries]
    if any(route not in {"auto_accepted", "manual_review"} for route in routes):
        raise DxfSplitError("DXF 拆板 CLI 返回了未知业务路由。")
    manual_review_count = routes.count("manual_review")
    if (completed.returncode == 0 and manual_review_count) or (
        completed.returncode == 1 and not manual_review_count
    ):
        raise DxfSplitError("DXF 拆板 CLI 退出码与业务路由不一致。")
    return {
        "schema": CLI_SCHEMA,
        "splitter_version": SPLITTER_VERSION,
        "status": ("completed_with_review" if completed.returncode == 1 else "completed"),
        "exit_code": completed.returncode,
        "input_count": expected_input_count,
        "auto_accepted_count": routes.count("auto_accepted"),
        "manual_review_count": manual_review_count,
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
