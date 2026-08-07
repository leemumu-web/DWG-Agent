"""BOX 左右进读取器。

从 BOX 拆板前 DXF 中按三步（定位主视图 -> 识别板件源边 -> 读取左右进）
提取上翼/下翼/腹板的左进与右进（mm，向下取整），输出与拆板对齐的
`翼`/`腹`（左右进相同合并 ×2）或 `上翼`/`下翼`/`上腹`/`下腹`（不同分列）。

主要入口：
- ``BoxAnalyzer.analyze(drawing)`` —— 单图分析；
- ``read_ezdxf(path)`` —— DXF -> DrawingData；
- ``analyze_manifest(entries)`` —— 批量分析；
- ``box-reader`` 命令 —— Excel/JSON 输出与 PNG 校验图。
"""

__version__ = "0.1.0"

from .analyzer import AnalyzerConfig, BoxAnalyzer
from .batch import (
    BoxBatchItem,
    BoxBatchOutcome,
    BoxInputEntry,
    BoxMeasurement,
    BoxProgress,
    analyze_manifest,
)
from .dxf_ezdxf import read_ezdxf
from .model import (
    BoardRole,
    BoxSpec,
    DrawingData,
    DrawingResult,
    LocalSegment,
    PlateMeasurement,
    Primitive,
    UnsupportedGeometry,
    ViewCandidate,
)
from .simple_xlsx import write_results_xlsx
from .units import insunits_info

__all__ = [
    "AnalyzerConfig",
    "BoardRole",
    "BoxAnalyzer",
    "BoxBatchItem",
    "BoxBatchOutcome",
    "BoxInputEntry",
    "BoxMeasurement",
    "BoxProgress",
    "BoxSpec",
    "DrawingData",
    "DrawingResult",
    "LocalSegment",
    "PlateMeasurement",
    "Primitive",
    "UnsupportedGeometry",
    "ViewCandidate",
    "__version__",
    "analyze_manifest",
    "insunits_info",
    "read_ezdxf",
    "write_results_xlsx",
]
