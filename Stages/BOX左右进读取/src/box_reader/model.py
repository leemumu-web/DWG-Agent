from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

Point = tuple[float, float]


class BoardRole(str, Enum):
    """BOX 的四个物理板件角色（与拆板 PhysicalPlateRole 对齐）。

    拆板命名：FLANGE_TOP→上翼、FLANGE_BOTTOM→下翼、
    WEB_LEFT→上腹、WEB_RIGHT→下腹。上腹/下腹是 BOX 左右两侧的腹板，
    从主视图正面投影重叠；成对（左右进相同）时合并输出 `腹板`（×2），
    与拆板 writer 的"腹"（WEB_LEFT+WEB_RIGHT 成对合并）一致。
    """

    TOP_FLANGE = "上翼"
    BOTTOM_FLANGE = "下翼"
    UPPER_WEB = "上腹"
    LOWER_WEB = "下腹"
    WEB = "腹板"  # 上腹+下腹成对合并输出


@dataclass(slots=True)
class Primitive:
    kind: str
    layer: str
    points: list[Point]
    source_block: str = ""
    source_handle: str = ""
    text: str = ""


@dataclass(frozen=True, slots=True)
class UnsupportedGeometry:
    kind: str
    layer: str
    source_block: str = ""
    source_handle: str = ""
    reason: str = ""


@dataclass(slots=True)
class DrawingData:
    path: Path
    primitives: list[Primitive]
    texts: list[Primitive]
    backend: str
    audit_messages: list[str] = field(default_factory=list)
    fatal_messages: list[str] = field(default_factory=list)
    unsupported_geometry: list[UnsupportedGeometry] = field(default_factory=list)
    insunits_code: int | None = None
    insunits_name: str = ""
    header_unit_to_mm: float | None = None


@dataclass(slots=True)
class BoxSpec:
    depth: float      # 截面深度 H（主视图高度方向）
    width: float      # 截面宽度 W（俯视图高度方向）
    flange_thickness: float  # 翼板厚 tf
    web_thickness: float     # 腹板厚 tw
    raw_text: str


@dataclass(slots=True)
class LocalSegment:
    a: Point
    b: Point
    layer: str
    source_block: str
    source_handle: str = ""


@dataclass(slots=True)
class ViewCandidate:
    view_id: str
    segments: list[LocalSegment]
    s_min: float
    s_max: float
    t_min: float
    t_max: float
    role: str = "unknown"  # "front"(主视图) / "top"(俯视图)
    unit_scale_to_mm: float = 1.0
    primitives: list[Primitive] = field(default_factory=list)

    @property
    def length(self) -> float:
        return self.s_max - self.s_min

    @property
    def height(self) -> float:
        return self.t_max - self.t_min


@dataclass(slots=True)
class PlateMeasurement:
    role: str
    left_raw: float
    right_raw: float
    left_safe: int
    right_safe: int
    confidence: float
    evidence: str


@dataclass(slots=True)
class DrawingResult:
    file_name: str
    part_number: str
    specification: str
    status: str
    confidence: float
    measurements: list[PlateMeasurement]
    warnings: list[str] = field(default_factory=list)
    diagnostics: dict[str, object] = field(default_factory=dict)
