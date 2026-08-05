"""Internal typed contracts shared by Excel workbook import and execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, NotRequired, TypedDict


@dataclass(frozen=True, slots=True)
class ExcelInputIssue:
    sheet: str | None
    row: int | None
    column: str | None
    field: str | None
    value: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class ExcelInputFailure:
    code: str
    message: str
    action: str
    contract_version: int
    issues: tuple[ExcelInputIssue, ...]
    sheets: tuple[str, ...]
    meta: dict[str, Any]

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "action": self.action,
            "contract_version": self.contract_version,
            "issues": [asdict(issue) for issue in self.issues],
            "sheets": list(self.sheets),
            "meta": dict(self.meta),
        }


@dataclass(frozen=True, slots=True)
class ExcelStage1Inspection:
    protocol_version: int
    input_contract_version: int
    source_format: str
    sheet_name: str | None
    header_row: int
    part_count: int
    component_count: int
    warnings: tuple[str, ...] = ()
    ignored_sheets: tuple[str, ...] = ()


class ExcelFinalPartType(StrEnum):
    PART = "part"
    PLATE = "plate"
    FLAT_BAR = "flat_bar"
    BBH = "bbh"
    BBH_WEB = "bbh_web"
    BBH_FLANGE = "bbh_flange"
    BH = "bh"
    BH_WEB = "bh_web"
    BH_FLANGE = "bh_flange"
    BOX = "box"
    BOX_WEB = "box_web"
    BOX_FLANGE = "box_flange"
    BT = "bt"
    BT_WEB = "bt_web"
    BT_FLANGE = "bt_flange"
    I_BEAM = "i_beam"
    H_BEAM = "h_beam"
    T_BEAM = "t_beam"
    CHANNEL = "channel"
    ANGLE = "angle"
    SQUARE_TUBE = "square_tube"
    STEEL_PIPE = "steel_pipe"
    SQUARE_BAR = "square_bar"
    HFW_PIPE = "hfw_pipe"
    W_BEAM = "w_beam"
    ROUND_BAR = "round_bar"
    REBAR = "rebar"
    BOLT = "bolt"
    NUT = "nut"
    THREADED_SLEEVE = "threaded_sleeve"
    TT = "tt"
    UNCLASSIFIED = "unclassified"


class HandbookCategory(StrEnum):
    FLAT_STEEL = "flat_steel"
    ROUND_BAR = "round_bar"
    REBAR = "rebar"
    SQUARE_BAR = "square_bar"
    I_BEAM = "i_beam"
    H_BEAM = "h_beam"
    T_BEAM = "t_beam"
    CHANNEL = "channel"
    ANGLE = "angle"
    STEEL_PIPE = "steel_pipe"
    SQUARE_TUBE = "square_tube"
    HFW_PIPE = "hfw_pipe"
    W_BEAM = "w_beam"
    PLATE = "plate"
    SKIP = "skip"


class WeightValidationStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    SEVERE_WARNING = "severe_warning"


class PartsImportStats(TypedDict):
    parts_imported: int
    error: NotRequired[str]


class ComponentsImportStats(TypedDict):
    components_imported: int


class QualityExpectation(TypedDict):
    quality_status: str
    warning_count: int
    severe_warning_count: int


class QualityImportStats(QualityExpectation):
    report_summary: dict[str, object] | None


class WorkbookImportStats(PartsImportStats, ComponentsImportStats, QualityImportStats):
    pass


class BatchImportStats(PartsImportStats, ComponentsImportStats):
    batch_id: int
    quality_status: str
    warning_count: int
    severe_warning_count: int
    report_summary: dict[str, object] | None
    total_net_weight: float | None
    total_gross_weight: float | None


__all__ = [
    "BatchImportStats",
    "ComponentsImportStats",
    "ExcelFinalPartType",
    "ExcelInputFailure",
    "ExcelInputIssue",
    "ExcelStage1Inspection",
    "HandbookCategory",
    "PartsImportStats",
    "QualityExpectation",
    "QualityImportStats",
    "WorkbookImportStats",
    "WeightValidationStatus",
]
