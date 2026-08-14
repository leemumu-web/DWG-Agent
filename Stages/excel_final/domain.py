"""Canonical records shared by the normalized Excel Final pipeline.

Cross-module input/evidence contract (reader → canonical_pipeline →
writer_parts → stage2). These frozen records must stay stable: any field
change ripples through the whole pipeline and the stage2 handoff.

Field conventions:

- ``component_qty`` is the 构件数 (component count) of the source row, while
  ``original_qty`` is the 数量 (quantity) column; they are distinct and both
  carried through validation.
- ``invalid_fields`` lists the missing/invalid source column names; a
  non-empty tuple means the row is not fully trusted. Consumers must treat
  None weight/length fields as "source value missing", never as zero.
- ``classification`` is the routed category and is None until the row has
  been classified; do not assume it is always set.
- Weights are unrounded Decimals on purpose: rounding happens only at
  writer/report boundaries (see weights.py tolerances).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class SourcePart:
    """One normalized source part row (构件行/零件行 input evidence).

    ``component_qty`` is 构件数, ``original_qty`` is 数量; ``invalid_fields``
    lists the missing source columns (see module docstring). Frozen: parts
    are passed through the pipeline without mutation.
    """

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
    invalid_fields: tuple[str, ...] = ()


class ComponentRowKind(StrEnum):
    START = "start"
    SUBTOTAL = "subtotal"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class ComponentSourceRow:
    """One normalized component-scoped source row (构件 row evidence).

    ``kind`` discriminates start/subtotal/summary rows of a component block;
    quantity and weight fields are None for non-data rows.
    """

    source_sheet: str
    source_row: int
    kind: ComponentRowKind
    batch: str | None
    component_no: str
    component_qty: Decimal | None
    original_spec: str | None
    material: str | None
    source_unit_net: Decimal | None
    source_total_net: Decimal | None
    source_unit_gross: Decimal | None
    source_total_gross: Decimal | None
    source_unit_area: Decimal | None
    source_total_area: Decimal | None
    component_length: Decimal | None
    component_width: Decimal | None
    component_height: Decimal | None
    subtotal_source_row: int | None = None


@dataclass(frozen=True, slots=True)
class ParentPartEvidence:
    """Evidence chain of one parent part before splitting.

    Carries the normalized spec, density source, unrounded theoretical
    weights and the weight-validation outcome; ``weight_validation_status``
    is one of the quality levels and drives isolation decisions downstream.
    """

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
    """One split child of a parent part (拆板子件).

    ``is_main`` marks the primary child; the unrounded theoretical
    contribution must sum exactly with the other children to the parent's
    theoretical weight (conservation check in splitter.py).
    """

    parent: ParentPartEvidence
    part_type: str
    import_component_no: str
    import_part_no: str
    spec: Decimal
    width: Decimal | None
    quantity: Decimal
    is_main: bool
    theoretical_unit_weight_unrounded: Decimal
    theoretical_contribution_unrounded: Decimal


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """Result of one canonical pipeline run: output path + quality summary.

    ``output_path`` is resolved absolute; ``report_summary`` is immutable
    (MappingProxyType) for safe cross-thread reads.
    """

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
