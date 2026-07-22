"""Canonical records shared by the normalized Excel Final pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SourcePart:
    source_sheet: str
    source_row: int
    source_seq: str | int | None
    batch: str | None
    component_no: str
    component_qty: Decimal
    part_no: str
    original_spec: str
    material: str
    length: Decimal
    original_qty: Decimal
    source_unit_net: Decimal | None
    source_total_net: Decimal | None
    source_unit_gross: Decimal | None
    source_total_gross: Decimal | None
    source_unit_area: Decimal | None
    source_total_area: Decimal | None
    classification: str | None


@dataclass(frozen=True, slots=True)
class ParentPartEvidence:
    source: SourcePart
    normalized_type: str
    normalized_spec: str
    normalized_width: Decimal | None
    density_value: Decimal | None
    density_source: str
    theoretical_unit_weight_unrounded: Decimal | None
    theoretical_total_weight_unrounded: Decimal | None
    material_utilization: Decimal | None
    weight_validation_status: str
    weight_validation_details: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SplitPart:
    parent: ParentPartEvidence
    part_type: str
    import_component_no: str
    import_part_no: str
    spec: Decimal
    width: Decimal | None
    quantity: Decimal
    is_main: bool
    theoretical_contribution_unrounded: Decimal


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    output_path: Path
    quality_status: str
    warning_count: int
    severe_warning_count: int
    report_summary: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_path", self.output_path.resolve())
        summary = dict(self.report_summary)
        category_counts = summary.get("category_counts")
        if isinstance(category_counts, Mapping):
            summary["category_counts"] = MappingProxyType(dict(category_counts))
        object.__setattr__(self, "report_summary", MappingProxyType(summary))

    def __fspath__(self) -> str:
        return str(self.output_path)
