"""Pydantic data models for DXF table extraction pipeline."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .config import DrawingType, RowType


class TextEntity(BaseModel):
    """A single TEXT entity extracted from DXF."""

    x: float
    y: float
    text: str  # raw text (may include \M+5XXXX escape sequences)
    height: float = 3.0
    layer: str = ""


class LineEntity(BaseModel):
    """A single LINE entity extracted from DXF."""

    x1: float
    y1: float
    x2: float
    y2: float
    layer: str = ""


class GridCell(BaseModel):
    """A single cell in the recovered table grid."""

    row: int
    col: int
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    texts: list[TextEntity] = Field(default_factory=list)
    merged_text: str = ""


class GridRow(BaseModel):
    """A row in the recovered table grid."""

    row_index: int
    y_min: float
    y_max: float
    cells: list[GridCell] = Field(default_factory=list)
    row_type: RowType = RowType.UNKNOWN
    confidence: float = 1.0


class ExtractedRow(BaseModel):
    """A fully parsed, decoded, normalized data row."""

    source_file: str
    drawing_type: DrawingType
    row_index: int
    row_subtype: str = "data"  # "data", "component_summary", "fastener_data"
    component_no: Optional[str] = None   # 构件号 (SKG), empty for B7
    part_no: Optional[str] = None
    spec: Optional[str] = None
    length_mm: Optional[float] = None
    material: Optional[str] = None
    quantity: Optional[int] = None
    unit_weight_kg: Optional[float] = None
    total_weight_kg: Optional[float] = None
    area_m2: Optional[float] = None
    remark: Optional[str] = None
    confidence: float = 1.0
    raw_cells: list[str] = Field(default_factory=list)


class TableResult(BaseModel):
    """Full result for one table extracted from one file."""

    source_file: str
    drawing_type: DrawingType
    source_block: str
    bbox_x1: float
    bbox_y1: float
    bbox_x2: float
    bbox_y2: float
    num_rows: int
    num_cols: int       # actual grid column count (including dividers)
    data_cols: int = 9  # actual data columns (excluding dividers)
    data_rows: list[ExtractedRow] = Field(default_factory=list)
    grid_rows: list[GridRow] = Field(default_factory=list)
    text_count: int = 0
    line_count: int = 0
    candidate_score: float = 0.0
    fill_rate: float = 0.0
    grid_score: float = 0.0
    grid_regularity: float = 0.0  # NEW: LINE clustering quality score


class WarningInfo(BaseModel):
    """Quality warning or anomaly."""

    source_file: str
    table_index: int
    row_index: int
    warning_code: str
    message: str
    raw_value: str = ""
