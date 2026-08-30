"""Subprocess boundary for the standalone PL splitter Stage."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PL_SPLITTER_VERSION = "0.2.0"
PL_REPORT_SCHEMA = "steel-dxf-split-pl-report/2"
PL_SOURCE_CONTRACT_ID = "project_tekla_pl_dxf_v1"


class PlSplitError(RuntimeError):
    """PL Stage invocation or response contract failure."""


@dataclass(frozen=True, slots=True)
class PlSplitterResult:
    exit_code: int
    payload: dict[str, Any]


def _resolved_directory(path: Path, *, label: str) -> Path:
    value = Path(path)
    if value.is_symlink() or not value.is_dir():
        raise PlSplitError(f"{label}目录不可用。")
    return value.resolve()


def invoke_pl_splitter(
    input_directory: Path,
    output_directory: Path,
    *,
    timeout_seconds: int,
) -> PlSplitterResult:
    """Invoke only ``steel_dxf_split_pl`` and verify its batch envelope."""
    input_root = _resolved_directory(input_directory, label="PL 拆板输入")
    output_root = _resolved_directory(output_directory, label="PL 拆板输出")
    expected_input_count = len(tuple(input_root.glob("*.dxf")))
    if expected_input_count <= 0:
        raise PlSplitError("PL Stage 不能接收空批次。")
    command = [
        sys.executable,
        "-m",
        "steel_dxf_split_pl.cli",
        str(input_root),
        "--output-dir",
        str(output_root),
        "--authorize-project-tekla-pl-dxf-v1",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PlSplitError("PL 拆板执行超时。") from exc
    if completed.returncode not in {0, 1}:
        message = completed.stderr.strip() or "PL 拆板进程异常退出。"
        raise PlSplitError(message)
    if completed.stderr.strip():
        raise PlSplitError("PL 拆板成功或安全拒绝时产生了非预期 stderr。")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PlSplitError("PL 拆板 CLI 未返回合法 JSON。") from exc
    if not isinstance(payload, dict) or payload.get("schema") != PL_REPORT_SCHEMA:
        raise PlSplitError("PL 拆板报告合同不匹配。")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != expected_input_count:
        raise PlSplitError("PL 拆板报告数量与冻结输入不一致。")
    if payload.get("exit_code") != completed.returncode:
        raise PlSplitError("PL 拆板报告退出码与进程不一致。")
    success_count = sum(
        isinstance(item, dict) and item.get("status") == "success" for item in items
    )
    rejected_count = sum(
        isinstance(item, dict) and item.get("status") == "rejected" for item in items
    )
    if (
        success_count + rejected_count != len(items)
        or payload.get("success_count") != success_count
        or payload.get("rejected_count") != rejected_count
    ):
        raise PlSplitError("PL 拆板报告状态计数不一致。")
    return PlSplitterResult(exit_code=completed.returncode, payload=payload)
