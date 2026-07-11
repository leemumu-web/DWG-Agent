"""Row-type detection: component start, component end, total row."""

from __future__ import annotations

import re

from utils import safe_str

# Pattern for typical part numbers: "15C-7", "15CSJ-11", "M20", "b7-s-12"
_PART_NO_RE = re.compile(
    r"^(\d+[A-Za-z]+-\d+.*|M\d+.*|[a-z]\d+-[a-z]-\d+.*)$"
)


def _looks_like_part_number(val: str) -> bool:
    """Heuristic: does *val* look like a part number (not a component ID)?"""
    if not val:
        return False
    return bool(_PART_NO_RE.match(val.strip()))


def is_component_start_row(
    ws, row: int, batch_col: int, comp_col: int, part_col: int
) -> bool:
    """Component START row.

    With 批次 column (standard Tekla):
      批次 filled + 构件编号 filled + 零件号 empty.
    Without 批次 column (space-delimited format):
      构件编号 filled AND does NOT look like a part number.
    """
    comp = safe_str(ws.cell(row=row, column=comp_col).value)
    part = safe_str(ws.cell(row=row, column=part_col).value)
    if batch_col and batch_col > 0:
        batch = safe_str(ws.cell(row=row, column=batch_col).value)
        return bool(batch) and bool(comp) and not bool(part)
    # No batch: distinguish component ID from part number
    return bool(comp) and not _looks_like_part_number(comp)


def is_component_end_row(ws, row: int, part_col: int) -> bool:
    """Component END row: 零件号 == '构件小计'."""
    part = safe_str(ws.cell(row=row, column=part_col).value)
    return part == "构件小计"


def is_total_row(ws, row: int, comp_col: int) -> bool:
    """Grand-total row: 构件编号 contains '合计'."""
    comp = safe_str(ws.cell(row=row, column=comp_col).value)
    return "合计" in comp
