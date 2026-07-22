"""Minimal runtime configuration owned by the standalone Excel Final Stage."""

from __future__ import annotations

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb.xls"
OUTPUT_DIR = BASE_DIR / "data/output"

# Used only to recognize whitespace-delimited Tekla text candidates.
KW_批次 = "批次"
KW_构件编号 = "构件编号"
KW_零件号 = "零件"
KW_数量 = "数量"
KW_材质 = "材质"

# The platform injects the read-only connection at the isolated process boundary.
DB_CONFIG: dict[str, object] = {}

# Unit suffixes are deliberately omitted so both forms match.
INIT_TABLE_SIGNATURE = [
    "零件号", "截面型材", "长度", "材质", "数量", "单重", "总重", "总面积", "备注",
]
