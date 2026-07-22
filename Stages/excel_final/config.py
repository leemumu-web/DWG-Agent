"""Project-wide constants, paths, column keywords, and DB configuration."""

from __future__ import annotations

from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────
# Base directory of this project
BASE_DIR = Path(__file__).resolve().parent

# Default input file (overridden by CLI argument)
DEFAULT_INPUT = BASE_DIR / "20260320-首都体育学院B7#地下部分-构件零件清单(毛净重)去gyb.xls"

# Output directory
OUTPUT_DIR = BASE_DIR / "data" / "output"

# ── Column keyword patterns for fuzzy matching ─────────────────
# Each keyword is tested as a substring against cleaned header text.
# Broader patterns match variant names (e.g. "零件" matches both 零件号 and 零件编号).
KW_批次 = "批次"
KW_构件编号 = "构件编号"
KW_零件号 = "零件"        # matches 零件号 / 零件编号
KW_规格 = "规格"
KW_型材 = "型材"          # matches 型材 / 截面型材 (alternative spec column name)
KW_长度 = "长度"
KW_材质 = "材质"
KW_数量 = "数量"
KW_单净重 = "单净重"
KW_总净重 = "总净重"
KW_单毛重 = "单毛重"
KW_总毛重 = "总毛重"
KW_单表面积 = "单表面"
KW_总表面积 = "总表面"
KW_宽度构件 = "宽度"      # component-dimension column (deleted in step 7)
KW_高度 = "高度"
KW_版本 = "版本"
KW_备注 = "备注"

# ── Expected header sequence after step 25 ─────────────────────
EXPECTED_HEADERS = [
    "序号", "构件编号", "构件数", "类型", "零件号", "截面型材",
    "规格", "宽度", "长度", "左进", "右进", "下料长度", "材质",
    "数量", "总数", "总长", "比重", "理单重", "理总重",
    "单净重", "总净重", "表净重", "单毛重", "总毛重", "表毛重",
    "单表面积", "总表面积",
]

# ── Unit suffix mapping for final headers ──────────────────────
HEADER_UNITS = {
    "长度": "长度(mm)",
    "宽度": "宽度(mm)",
    "左进": "左进(mm)",
    "右进": "右进(mm)",
    "下料长度": "下料长度(mm)",
    "总长": "总长(mm)",
    "单净重": "单净重(kg)",
    "总净重": "总净重(kg)",
    "表净重": "表净重(kg)",
    "单毛重": "单毛重(kg)",
    "总毛重": "总毛重(kg)",
    "表毛重": "表毛重(kg)",
    "理单重": "理单重(kg)",
    "理总重": "理总重(kg)",
    "单表面积": "单表面积(㎡)",
    "总表面积": "总表面积(㎡)",
    "比重": "比重(kg/m)",
}

# ── Hardware Handbook DB ───────────────────────────────────────
# The platform injects the read-only connection at the process boundary.
DB_CONFIG: dict[str, object] = {}

# 初始表 format signature headers (for auto-detection)
INIT_TABLE_SIGNATURE = [
    "零件号", "截面型材", "长度(mm)", "材质", "数量",
    "单重(kg)", "总重(kg)", "总面积(m2)", "备注",
]
