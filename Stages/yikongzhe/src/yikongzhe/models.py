"""核心数据模型。"""

from dataclasses import dataclass, field
from enum import Enum


class ShapeType(Enum):
    RECTANGLE = "方"
    IRREGULAR = "异"


class HoleType(Enum):
    WITH_HOLE = "有孔"
    WITHOUT_HOLE = "无孔"


class BendType(Enum):
    WITH_BEND = "有折"
    WITHOUT_BEND = "无折"


@dataclass
class Part:
    """单块板的数据模型。

    Attributes:
        name: 板件名称，如 "p=2b1-cb-18腹"、"p=2b1-cb-18翼-1"。
        dxf_file: 来源 DXF 文件名。
        is_web: True 表示腹板，False 表示翼板。
        text_position: TEXT 标注在 DXF 中的坐标 (x, y)。
        entities: 属于该板的几何实体列表，每个元素为 ezdxf Entity 对象。
    """
    name: str
    dxf_file: str
    is_web: bool
    text_position: tuple[float, float]
    entities: list = field(default_factory=list)


@dataclass
class PartClassification:
    """单块板的分类结果。

    Attributes:
        part_name: 板件名称。
        dxf_file: 来源 DXF 文件名。
        shape: 方/异。
        hole: 有孔/无孔。
        bend: 有折/无折。
        category: 最终类别名，如 "方孔折"。
    """
    part_name: str
    dxf_file: str
    shape: ShapeType
    hole: HoleType
    bend: BendType
    category: str


@dataclass
class DxfResult:
    """单个 DXF 文件的处理结果。

    Attributes:
        dxf_file: DXF 文件名。
        parts: 该文件中所有板件的分类结果。
    """
    dxf_file: str
    parts: list[PartClassification] = field(default_factory=list)