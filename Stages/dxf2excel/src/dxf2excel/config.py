"""Constants, thresholds, and column definitions for DXF table extraction.

Column model: variable-width material tables (9-11 grid columns).
- 9-col (B7 GGZ/MSZJ): part_no | spec | length | material | quantity | unit_wt | total_wt | area | remark
- 10-col (SKG): component_no | part_no | spec | length | material | quantity | unit_wt | total_wt | area | remark
- 11-col grid: same as 10-col plus a narrow divider between quantity and unit_wt

The algorithm detects actual column count at runtime.  raw_like_original preserves
the original column count; all_rows maps to a standardized superset of fields.
"""

from enum import Enum
from typing import Final

# ---- Standardized field keys (superset) ----
ALL_FIELD_KEYS: Final[list[str]] = [
    "component_no",   # 构件号 (SKG only, empty for B7)
    "part_no",        # 零件号
    "spec",           # 截面型材
    "length_mm",      # 长度(mm)
    "material",       # 材质
    "quantity",       # 数量
    "unit_weight_kg", # 单重(kg)
    "total_weight_kg",# 总重(kg)
    "area_m2",        # 总面积(m2)
    "remark",         # 备注
]

# Legacy 9-column keys (B7 project).  Maps to ALL_FIELD_KEYS[1:].
COLUMN_KEYS_9: Final[list[str]] = [
    "part_no", "spec", "length_mm", "material",
    "quantity", "unit_weight_kg", "total_weight_kg", "area_m2", "remark",
]

# Expected column boundary X positions for each scale family (fallback only).
# Runtime LINE clustering takes priority.
EXPECTED_COLUMN_X_FAMILIES: Final[list[list[float]]] = [
    # B7 scale: 9 data cols (10 boundaries)
    [373.5, 397.0, 428.5, 443.0, 463.0, 471.0, 484.0, 499.5, 517.0, 539.0],
    # SKG compact scale: 10 data cols + divider (12 boundaries)
    [432, 447, 458, 477, 486, 496, 500, 502, 512, 522, 530, 539],
    # SKG large scale
    [8630, 8940, 9150, 9540, 9720, 9910, 10010, 10040, 10230, 10430, 10610, 10780],
    # SKG extra-large scale
    [10315, 10470, 10575, 10770, 10860, 10955, 11005, 11020, 11115, 11215, 11305, 11390],
]

# ---- Tolerance constants (now relative where possible) ----
# Fixed floor values — used when text height is unavailable
Y_CLUSTER_TOLERANCE_FLOOR: Final[float] = 0.2
X_CLUSTER_TOLERANCE_FLOOR: Final[float] = 0.2
ROW_HEIGHT_MIN_RATIO: Final[float] = 0.6   # × median_row_height → min gap
TEXT_INSET_RATIO: Final[float] = 0.15       # × median_text_height → margin

# Legacy fixed tolerances (used when adaptive can't compute)
Y_CLUSTER_TOLERANCE: Final[float] = 0.3
X_CLUSTER_TOLERANCE: Final[float] = 0.3
ROW_HEIGHT_MIN: Final[float] = 3.0
ROW_HEIGHT_TYPICAL: Final[float] = 5.0
TEXT_INSET_TOLERANCE: Final[float] = 0.5

# ---- Table candidate scoring ----
SCORE_TEXT_MIN: Final[int] = 10
SCORE_LINE_MIN: Final[int] = 20
SCORE_ENTITY_MIN: Final[int] = 50
# NOTE: SCORE_ENTITY_MAX (500) REMOVED.
# Large tables (up to ~1200 entities) are valid material tables.
# Structural drawings are excluded by T/L ratio (≈0.03) and grid irregularity.

# ---- Grid regularity scoring (replaces entity cap) ----
GRID_COL_COUNT_MIN: Final[int] = 7
GRID_COL_COUNT_MAX: Final[int] = 14

# ---- Row classification patterns ----
HEADER_KEYWORDS: Final[list[str]] = [
    "零件号", "截面型材", "长度", "材质", "数量",
    "单重", "总重", "总面积", "备注", "构件号",
]
SUMMARY_KEYWORDS: Final[list[str]] = ["合计", "总计", "小计"]
TOTAL_ROW_PATTERNS: Final[list[str]] = ["合计", "总计"]

# ---- Fastener / special row detection ----
FASTENER_SPEC_PATTERNS: Final[list[str]] = [
    r"^M\s+\d+",        # M 20 X 90, M 30 X 120
    r"^STUD",            # STUD
    r"^NUT_",            # NUT_M30
    r"^D\d+",            # D19, D30
]
FASTENER_MATERIALS: Final[set[str]] = {"C", "STUD", "TS10.9", "TS8.8"}

# ---- Quality validation thresholds ----
WEIGHT_TOLERANCE_RATIO: Final[float] = 0.02  # 2% tolerance
MIN_FILL_RATE: Final[float] = 0.5

# ---- Drawing type ----
class DrawingType(str, Enum):
    GGZ = "GGZ"
    MSZJ = "MSZJ"
    SKG = "SKG"
    UNKNOWN = "unknown"

# ---- Row type ----
class RowType(str, Enum):
    HEADER = "header"
    SUBHEADER = "subheader"
    COMPONENT_SUMMARY = "component_summary"  # 构件号行：仅col 0非空
    DATA = "data"
    FASTENER_DATA = "fastener_data"           # 紧固件行：无part_no, spec含M/STUD
    SUMMARY = "summary"
    TOTAL = "total"
    EMPTY = "empty"
    UNKNOWN = "unknown"

# ---- Warning codes ----
class WarnCode(str, Enum):
    # Validation errors
    WEIGHT_MISMATCH = "WARN_WEIGHT_MISMATCH"
    DUPLICATE_PART = "WARN_DUPLICATE_PART"
    EMPTY_REQUIRED = "WARN_EMPTY_REQUIRED"
    LENGTH_RANGE = "WARN_LENGTH_RANGE"
    QUANTITY_RANGE = "WARN_QUANTITY_RANGE"
    LOW_FILL = "WARN_LOW_FILL"
    HEADER_MISMATCH = "WARN_HEADER_MISMATCH"
    NO_TOTAL_ROW = "WARN_NO_TOTAL_ROW"
    DECODE_FAILURE = "WARN_DECODE_FAILURE"
    GRID_IRREGULAR = "WARN_GRID_IRREGULAR"
    NO_TABLE_FOUND = "WARN_NO_TABLE_FOUND"
    # Structural / branch indicators
    SCHEMA_N_COLS = "WARN_SCHEMA_N_COLS"           # non-9-column table detected
    COMPONENT_MERGED = "WARN_COMPONENT_MERGED"      # 构件号合并语义
    FASTENER_ROW = "WARN_FASTENER_ROW"              # 紧固件行 (part_no可空)
    INSERT_TRANSFORM = "WARN_INSERT_TRANSFORM"       # 非原点INSERT
    LARGE_TABLE = "WARN_LARGE_TABLE"                # 大表 (entity > 500)
    TEXT_ENCODING_MIXED = "WARN_TEXT_ENCODING_MIXED" # 混合编码
    PROCESSING_ERROR = "WARN_PROCESSING_ERROR"

# ---- Header alias normalization ----
HEADER_ALIASES: Final[dict[str, str]] = {
    # Column name variants → canonical field key
    "零件号": "part_no",
    "零件编号": "part_no",
    "截面型材": "spec",
    "规格": "spec",
    "长度(mm)": "length_mm",
    "长度": "length_mm",
    "材质": "material",
    "材料": "material",
    "数量": "quantity",
    "单重(kg)": "unit_weight_kg",
    "单重(Kg)": "unit_weight_kg",
    "单重": "unit_weight_kg",
    "总重(kg)": "total_weight_kg",
    "总重(Kg)": "total_weight_kg",
    "总重": "total_weight_kg",
    "总面积(m2)": "area_m2",
    "面积(m2)": "area_m2",
    "面积": "area_m2",
    "备注": "remark",
    "构件号": "component_no",
}

# ---- Material grade patterns ----
MATERIAL_GRADE_RE: Final[str] = (
    r"^Q\d{3}[A-Z]?(-Z\d{2})?$"  # Q355B, Q345GJB-Z25, Q345GJB-Z35
    r"|^[A-Z]{2,}$"               # STUD, TS10.9, C
    r"|^TS\d+\.\d+$"              # TS10.9
)
