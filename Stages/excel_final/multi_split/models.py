"""Data models for SunFire steel fabrication processing."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SortSpec:
    """Single sort condition.

    Maps to one ComboBox + OptionButton pair in SortCriteria form.
    """
    column: str          # Column name to sort by
    ascending: bool = True


@dataclass
class ColumnMapping:
    """Maps the 12 standard keywords to actual Excel column names.

    These 12 keywords are used by the BOM maker (qdmade) to auto-detect
    which column contains what data.  The values here are the keywords
    that are searched for via substring match in the header row.
    """
    drawing_no: str = "图号"
    component_no: str = "构件号"
    component_qty: str = "构件数量"
    part_no: str = "零件号"
    spec: str = "规格"
    width: str = "宽度"
    length: str = "长度"
    material: str = "材质"
    total_parts: str = "零件总数"
    total_weight: str = "总重"
    part_type: str = "零件类型"
    manufacturer: str = "制作单位"

    def to_keyword_list(self) -> list[tuple[str, str]]:
        """Return ordered list of (field_name, keyword) pairs."""
        return [
            ("drawing_no", self.drawing_no),
            ("component_no", self.component_no),
            ("component_qty", self.component_qty),
            ("part_no", self.part_no),
            ("spec", self.spec),
            ("width", self.width),
            ("length", self.length),
            ("material", self.material),
            ("total_parts", self.total_parts),
            ("total_weight", self.total_weight),
            ("part_type", self.part_type),
            ("manufacturer", self.manufacturer),
        ]
