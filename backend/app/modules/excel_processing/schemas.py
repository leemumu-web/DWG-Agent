"""Internal typed contracts shared by Excel workbook import and execution."""

from __future__ import annotations

from enum import StrEnum
from typing import NotRequired, TypedDict


class ExcelFinalPartType(StrEnum):
    PART = "part"
    PLATE = "plate"
    FLAT_BAR = "flat_bar"
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
    ROUND_BAR = "round_bar"
    REBAR = "rebar"
    BOLT = "bolt"
    NUT = "nut"
    THREADED_SLEEVE = "threaded_sleeve"
    TT = "tt"
    UNCLASSIFIED = "unclassified"


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
    "PartsImportStats",
    "QualityExpectation",
    "QualityImportStats",
    "WeightValidationStatus",
]
