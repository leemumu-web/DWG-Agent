"""异孔折判断 — DXF板件图形分类工具。

对拆板后的DXF文件进行批量分类，判断每块板的三个维度：
- 方/异（外轮廓形状）
- 有孔/无孔（内部孔洞）
- 有折/无折（腹板折弯特征传递到翼板）
"""

from yikongzhe.models import (
    BendType,
    DxfResult,
    HoleType,
    Part,
    PartClassification,
    ShapeType,
)
from yikongzhe.classifier import classify_directory

__all__ = [
    "BendType",
    "DxfResult",
    "HoleType",
    "Part",
    "PartClassification",
    "ShapeType",
    "classify_directory",
]