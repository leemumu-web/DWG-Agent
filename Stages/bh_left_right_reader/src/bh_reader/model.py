from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

Point = tuple[float, float]


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
    """A geometric entity the reader cannot safely reduce to source edges."""

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
class BHSpec:
    depth: float
    width: float
    web_thickness: float
    flange_thickness: float
    raw_text: str
    depth_end: float | None = None
    nominal_length: float | None = None
    drawing_scale_text: str | None = None
    drawing_scale_denominator: float | None = None

    @property
    def depth_min(self) -> float:
        return min(self.depth, self.depth_end if self.depth_end is not None else self.depth)

    @property
    def depth_max(self) -> float:
        return max(self.depth, self.depth_end if self.depth_end is not None else self.depth)

    @property
    def is_tapered(self) -> bool:
        return self.depth_end is not None and abs(self.depth_end - self.depth) > 1e-9


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
    axis: Point
    normal: Point
    s_min: float
    s_max: float
    t_min: float
    t_max: float
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    unit_scale_to_mm: float = 1.0

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
