"""Steel DXF Classifier 1.1 process, schema and naming contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.platform.config.settings import settings

CLASSIFIER_VERSION = "1.2.0"
REPORT_SCHEMA = "STEEL-DXF-CLASSIFICATION-1.2"
CLI_SCHEMA = "STEEL-DXF-CLI-1.2"
ERROR_CODE_CLASSIFICATION_FAILED = "DXF_CLASSIFICATION_FAILED"
ERROR_CODE_CLASSIFICATION_CONTRACT = "DXF_CLASSIFICATION_CONTRACT_INVALID"


class ClassificationError(RuntimeError):
    """Classifier failure whose message may be persisted as operator feedback."""


def classifier_project_name(project_code: str, workflow_id: int) -> str:
    return f"{project_code}-workflow-{workflow_id}"


def preprocessed_name(name: str) -> str:
    """Apply the classifier 1.1 input naming convention exactly once."""
    source = Path(name).name
    if Path(source).suffix.lower() != ".dxf":
        raise ClassificationError(f"分类输入不是 DXF: {source}")
    stem = Path(source).stem
    if not stem.endswith("_拆板前"):
        stem = f"{stem}_拆板前"
    return f"{stem}.dxf"


def invoke_classifier(input_directory: Path) -> dict[str, Any]:
    """Run the versioned CLI and validate its exit-code/JSON envelope."""
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "steel_dxf_classifier.cli",
                "--json",
                str(input_directory),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=settings.dxf_classification_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClassificationError("DXF 分类器执行超时。") from exc
    if completed.returncode not in {0, 2}:
        message = completed.stderr.strip() or "DXF 分类器执行失败。"
        raise ClassificationError(message.removeprefix("错误: ").strip())
    if completed.stderr.strip():
        raise ClassificationError("DXF 分类器成功退出时产生了非预期 stderr。")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ClassificationError("DXF 分类器未返回合法 JSON。") from exc
    if payload.get("schema") != CLI_SCHEMA or payload.get("exit_code") != completed.returncode:
        raise ClassificationError("DXF 分类器 CLI schema 或退出码不符合 1.2 契约。")
    return payload


def safe_route(project_name: str, route: object) -> str:
    """Validate a classifier output-directory name before path composition."""
    if not isinstance(route, str) or Path(route).name != route:
        raise ClassificationError("分类报告包含非法输出目录。")
    if not route.startswith(f"{project_name}_") or not route.endswith("_dxf"):
        raise ClassificationError("分类输出目录不符合 1.1 命名契约。")
    return route
