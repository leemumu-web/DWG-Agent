from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from .geometry_types import BoundingBox, Point2D


class BHPlateRole(str, Enum):
    WEB = "web"
    FLANGE = "flange"

    @property
    def canonical_zh(self) -> str:
        return {BHPlateRole.WEB: "腹板", BHPlateRole.FLANGE: "翼缘板"}[self]


@dataclass(frozen=True, slots=True)
class HProfile:
    height: float
    flange_width: float
    web_thickness: float
    flange_thickness: float
    raw_text: str
    secondary_height: float | None = None

    @property
    def is_variable_height(self) -> bool:
        return self.secondary_height is not None and abs(self.secondary_height - self.height) > 1e-9

    @property
    def max_height(self) -> float:
        return max(self.height, self.secondary_height or self.height)

    @property
    def min_height(self) -> float:
        return min(self.height, self.secondary_height or self.height)

    @property
    def clear_web_height(self) -> float:
        """Maximum nominal clear-web height across the member."""
        return self.max_height - 2.0 * self.flange_thickness

    @property
    def minimum_clear_web_height(self) -> float:
        return self.min_height - 2.0 * self.flange_thickness


@dataclass(frozen=True, slots=True)
class BHMetadata:
    part_number: str
    profile: HProfile
    nominal_length: float
    material: str | None
    drawing_scale: float
    material_table_handle: str | None = None


@dataclass(frozen=True, slots=True)
class BulgeVertex:
    x: float
    y: float
    bulge: float = 0.0

    @property
    def point(self) -> Point2D:
        return Point2D(self.x, self.y)


@dataclass(slots=True)
class BulgeContour:
    vertices: list[BulgeVertex]
    closed: bool = True

    def __post_init__(self) -> None:
        if len(self.vertices) < 3:
            raise ValueError("A closed contour requires at least three vertices.")

    @property
    def bbox(self) -> BoundingBox:
        return BoundingBox.from_points([vertex.point for vertex in self.vertices])

    def translated(self, dx: float, dy: float) -> "BulgeContour":
        return BulgeContour(
            [BulgeVertex(v.x + dx, v.y + dy, v.bulge) for v in self.vertices],
            self.closed,
        )

    def normalized(self) -> "BulgeContour":
        bbox = self.bbox
        return self.translated(-bbox.min_x, -bbox.min_y)


@dataclass(frozen=True, slots=True)
class CircularCut:
    center: Point2D
    radius: float

    def translated(self, dx: float, dy: float) -> "CircularCut":
        return CircularCut(self.center.translated(dx, dy), self.radius)


@dataclass(slots=True)
class BHPlate:
    role: BHPlateRole
    contour: BulgeContour
    thickness: float
    label: str
    quantity: int = 1
    circular_cuts: list[CircularCut] = field(default_factory=list)
    inner_contours: list[BulgeContour] = field(default_factory=list)
    source_index: int = 0
    area_mm2: float = 0.0
    provenance: dict[str, object] = field(default_factory=dict)

    @property
    def bbox(self) -> BoundingBox:
        return self.contour.bbox

    def translated(self, dx: float, dy: float) -> "BHPlate":
        return BHPlate(
            role=self.role,
            contour=self.contour.translated(dx, dy),
            thickness=self.thickness,
            label=self.label,
            quantity=self.quantity,
            circular_cuts=[cut.translated(dx, dy) for cut in self.circular_cuts],
            inner_contours=[contour.translated(dx, dy) for contour in self.inner_contours],
            source_index=self.source_index,
            area_mm2=self.area_mm2,
            provenance=dict(self.provenance),
        )


@dataclass(slots=True)
class BHAssembly:
    metadata: BHMetadata
    web_plate: BHPlate
    flange_plates: list[BHPlate]
    retained_insert_handles: list[str]
    diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def plates(self) -> list[BHPlate]:
        return [self.web_plate, *self.flange_plates]
