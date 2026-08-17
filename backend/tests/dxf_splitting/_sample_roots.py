"""外部 DXF 样本路径解析与缺失守卫。

PR #21 的 BH 图纸回归测试引用 PR 作者 Windows 工作机
(``D:\\Documents\\Codex\\...``) 上的外部 DXF 样本,这些样本不进仓库。
Linux / CI 没有样本时测试跳过;可用环境变量把样本根指向 Linux
本地路径以运行图纸测试:

- ``BH_A1_SAMPLE_ROOT``   a1-4 根因定位样本根(02_转换DXF)
- ``BH_B4_SAMPLE_ROOT``   DWG-Agent 拆板问题样本根(01_全部原文件)
- ``BH_DIAG_SAMPLE_ROOT`` BH/BOX 三组手工标准诊断样本根(01_DXF)

改动仅涉及测试路径与跳过守卫,不改变任何拆板逻辑。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_BH_A1_WINDOWS = (
    r"D:\Documents\Codex\a1-4问题4-8_根因定位_2026-08-15\02_转换DXF"
)
_BH_B4_WINDOWS = (
    r"D:\Documents\Codex\DWG-Agent拆板问题样本\最终交付\01_全部原文件"
)
_BH_DIAG_WINDOWS = (
    r"D:\Documents\Codex\BH_BOX_三组手工标准诊断_2026-08-17\01_DXF"
)


def _resolve_root(env_var: str, windows_default: str) -> Path:
    """样本根目录:优先环境变量(可指向 Linux 路径),否则 Windows 默认路径。"""
    return Path(os.environ.get(env_var, windows_default))


def a1_sample_root() -> Path:
    return _resolve_root("BH_A1_SAMPLE_ROOT", _BH_A1_WINDOWS)


def b4_sample_root() -> Path:
    return _resolve_root("BH_B4_SAMPLE_ROOT", _BH_B4_WINDOWS)


def diag_sample_root() -> Path:
    return _resolve_root("BH_DIAG_SAMPLE_ROOT", _BH_DIAG_WINDOWS)


def require_sample(source: Path) -> Path:
    """样本文件缺失时跳过测试;返回规范化的 source 供 load_document 使用。"""
    if not source.is_file():
        pytest.skip(
            f"外部 DXF 样本缺失:{source}。样本只在 PR 作者 Windows 工作机;"
            "可设置环境变量把样本根指向 Linux 本地路径。"
        )
    return source
