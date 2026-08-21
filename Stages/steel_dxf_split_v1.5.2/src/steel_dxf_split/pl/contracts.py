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
class DevelopmentTarget:
    projection_length_mm: float
    k_length_mm: float
    bom_length_mm: float
    raw_length_mm: float
    target_length_mm: float
    total_extension_mm: float


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
class StationBand:
    index: int
    upper_x_mm: float
    lower_x_mm: float
    source_entity_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LongitudinalIntervalEvidence:
    index: int
    left_station: StationBand
    right_station: StationBand
    upper_entity_indices: tuple[int, ...]
    lower_entity_indices: tuple[int, ...]
    upper_span_mm: float
    lower_span_mm: float
    upper_delta_y_mm: float
    lower_delta_y_mm: float
    is_end_feature: bool
    is_turn_candidate: bool
    source_handles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LongitudinalProof:
    intervals: tuple[LongitudinalIntervalEvidence, ...]
    carrier_interval_indices: tuple[int, ...]
    selection_reason: str


@dataclass(frozen=True, slots=True)
class DevelopedIntervalMetrics:
    index: int
    source_upper_span_mm: float
    source_lower_span_mm: float
    output_upper_span_mm: float
    output_lower_span_mm: float
    downstream_shift_mm: float
    is_carrier: bool


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


@dataclass(frozen=True, slots=True)
class DevelopedPlate:
    metadata: PLMetadata
    outline: PlateOutline
    section: SectionProof
    transformed_entities: tuple[DXFEntity, ...]
    metrics: DevelopmentMetrics


@dataclass(frozen=True, slots=True)
class PLWriteResult:
    output_path: Path
    min_x_mm: float
    length_mm: float
    width_mm: float
    label: str
    entity_type_counts: tuple[tuple[str, int], ...]
    audit_error_count: int


@dataclass(frozen=True, slots=True)
class PLCompilation:
    developed: DevelopedPlate
    write_result: PLWriteResult


@dataclass(frozen=True, slots=True)
class PLItemResult:
    status: str
    source_path: Path
    context_id: str
    part_number: str | None
    output_path: Path | None
    compilation: PLCompilation | None
    error_code: str | None
    error_message_zh: str | None


@dataclass(frozen=True, slots=True)
class PLBatchResult:
    input_path: Path
    output_dir: Path
    report_path: Path
    items: tuple[PLItemResult, ...]

    @property
    def success_count(self) -> int:
        return sum(item.status == "success" for item in self.items)

    @property
    def rejected_count(self) -> int:
        return sum(item.status == "rejected" for item in self.items)

    @property
    def exit_code(self) -> int:
        return 1 if self.rejected_count else 0
