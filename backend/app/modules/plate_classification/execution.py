"""Invoke yikongzhe CLI as subprocess for plate classification."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# 分类结果表名 → 中文标签
CATEGORY_LABELS = ["方", "异", "方孔", "异孔", "方折", "异折", "方孔折", "异孔折"]


def run_plate_classification(
    input_dir: str,
    output_path: str,
    *,
    encoding: str = "utf-8",
    stage_dir: str | None = None,
) -> dict:
    """调用 yikongzhe CLI 对目录下 DXF 文件执行分类。

    Args:
        input_dir: 含 DXF 文件的输入目录。
        output_path: 输出 Excel 路径。
        encoding: DXF 编码。
        stage_dir: Stage 包所在目录，默认自动检测。

    Returns:
        分类汇总: {dxf_count, total_parts, categories: {name: count}, items: [...]}
    """
    if stage_dir is None:
        stage_dir = str(Path(__file__).resolve().parents[4] / "Stages" / "yikongzhe")

    cmd = [
        "uv", "run", "--directory", stage_dir,
        "python", "-m", "yikongzhe",
        input_dir,
        "--output", output_path,
        "--encoding", encoding,
    ]

    logger.info("Running yikongzhe CLI: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        raise RuntimeError(
            f"yikongzhe CLI failed (rc={result.returncode}): {result.stderr}"
        )

    # Parse output to extract structured results
    return _parse_cli_output(result.stdout, result.stderr)


def _parse_cli_output(stdout: str, stderr: str) -> dict:
    """从 CLI 输出和 Excel 中提取结构化分类结果。

    当前版本通过 CLI stderr 日志解析，后续可通过 Excel 读取。
    """
    dxf_count = 0
    total_parts = 0

    for line in stderr.splitlines():
        if "处理完成:" in line:
            # "处理完成: N 个 DXF, M 块板"
            parts = line.split("处理完成:")[1].strip()
            dxf_part, parts_part = parts.split(",")
            dxf_count = int(dxf_part.strip().split()[0])
            total_parts = int(parts_part.strip().split()[0])

    return {
        "dxf_count": dxf_count,
        "total_parts": total_parts,
        "output_excel": None,  # Will be set after file registration
    }
