from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ezdxf.entities import DXFEntity
from shapely.geometry import Polygon


class PLSplitError(ValueError):
    def __init__(self, code: str, message_zh: str) -> None:
        super().__init__(message_zh)
        self.code = code
        self.message_zh = message_zh


@dataclass(frozen=True, slots=True)
class DevelopmentMetrics:
    projection_length_mm: float
    surface_lengths_mm: tuple[float, float]
    k_factor: float
    k_length_mm: float
    bom_length_mm: float
    raw_length_mm: float
    target_length_mm: float
    scale_x: float
    anchor_x_mm: float


@dataclass(frozen=True, slots=True)
class PLSourceContext:
    source_path: Path
    context_id: str
    container_handle: str | None
    entities: tuple[DXFEntity, ...]


@dataclass(frozen=True, slots=True)
class PLMetadata:
    part_number: str
    thickness_mm: float
    width_mm: float
    bom_length_mm: float


@dataclass(frozen=True, slots=True)
class PlateOutline:
    outer_entities: tuple[DXFEntity, ...]
    polygon: Polygon
    projection_length_mm: float
    width_mm: float
    anchor_x_mm: float
    source_handles: tuple[str, ...]
    candidate_count: int


@dataclass(frozen=True, slots=True)
class SectionProof:
    polygon: Polygon
    k_length_mm: float
    equivalent_surface_lengths_mm: tuple[float, float]
    proof_method: str
    source_handles: tuple[str, ...]
    candidate_count: int
