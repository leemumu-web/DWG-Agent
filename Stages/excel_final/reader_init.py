"""Read 初始表 (initial table) format — the 9-column flat format from DWG extraction.

Row 1: component info text → ComponentInfo
Row 2: column headers (零件号/截面型材/长度/材质/数量/单重/总重/总面积/备注)
Row 3+: part data rows, terminated by 合计 row
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl

from utils import safe_float, safe_str

# ── Dataclasses ──────────────────────────────────────────────────


@dataclass
class ComponentInfo:
    """Parsed component metadata from Row 1."""
    component_no: str       # e.g. "B7-4FD-ZL-19"
    component_qty: int      # e.g. 1
    total_weight: float     # e.g. 1739.26
    raw_text: str           # full Row 1 text


@dataclass
class PartRow:
    """A single part data row from the initial table."""
    part_no: str            # 零件号
    spec: str               # 截面型材 (original spec string)
    length: float | None    # 长度(mm)
    material: str           # 材质
    qty: float | None       # 数量
    unit_weight: float | None   # 单重(kg)
    total_weight: float | None  # 总重(kg)
    surface_area: float | None  # 总面积(m2)
    note: str               # 备注
    original_seq: int       # 1-based position in data rows

# ── Public API ───────────────────────────────────────────────────


def read_init_table(filepath: str | Path) -> tuple[ComponentInfo, list[PartRow]]:
    """Read an 初始表-format .xlsx file.

    Returns (ComponentInfo, list of PartRow).
    """
    filepath = Path(filepath)
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)

    # Locate the 初始表 sheet
    if "初始表" in wb.sheetnames:
        ws = wb["初始表"]
    else:
        ws = wb.worksheets[0]

    # Parse Row 1: component info
    row1_text = _join_row(ws, 1)
    comp_info = parse_component_info(row1_text)

    # Read data rows from Row 3 until 合计
    parts: list[PartRow] = []
    seq = 0
    for row_idx in range(3, ws.max_row + 1):
        vals = [_cell_str(ws, row_idx, c) for c in range(1, 10)]
        part_no = vals[0]
        spec = vals[1]

        # Stop at 合计 row
        if "合计" in part_no or "合计" in spec:
            break

        # Skip empty rows
        if not part_no and not spec:
            continue

        seq += 1
        parts.append(PartRow(
            part_no=part_no,
            spec=spec,
            length=safe_float(ws.cell(row=row_idx, column=3).value),
            material=vals[3],
            qty=safe_float(ws.cell(row=row_idx, column=5).value),
            unit_weight=safe_float(ws.cell(row=row_idx, column=6).value),
            total_weight=safe_float(ws.cell(row=row_idx, column=7).value),
            surface_area=safe_float(ws.cell(row=row_idx, column=8).value),
            note=vals[8],
            original_seq=seq,
        ))

    wb.close()
    return comp_info, parts


def parse_component_info(text: str) -> ComponentInfo:
    """Parse component metadata from Row 1 text.

    Example input:
        "B7-4FD-ZL-19材  料  表构件数量：1构件总重：1739.26"

    Returns ComponentInfo with extracted fields.
    """
    text = text.strip()

    # 构件号: everything before the first "材"
    component_no = ""
    m = re.match(r"^(.*?)材", text)
    if m:
        component_no = m.group(1).strip()

    # 构件数量
    component_qty = 0
    m = re.search(r"构件数量[：:]\s*(\d+)", text)
    if m:
        component_qty = int(m.group(1))

    # 构件总重
    total_weight = 0.0
    m = re.search(r"构件总重[：:]\s*([\d.]+)", text)
    if m:
        total_weight = float(m.group(1))

    return ComponentInfo(
        component_no=component_no,
        component_qty=component_qty,
        total_weight=total_weight,
        raw_text=text,
    )


# ── Internal helpers ─────────────────────────────────────────────


def _cell_str(ws, row: int, col: int) -> str:
    """Get a cell value as a cleaned string."""
    return safe_str(ws.cell(row=row, column=col).value)


def _join_row(ws, row: int) -> str:
    """Join all cells in a row into a single string."""
    parts = []
    for c in range(1, ws.max_column + 1):
        v = safe_str(ws.cell(row=row, column=c).value)
        if v:
            parts.append(v)
    return " ".join(parts)
