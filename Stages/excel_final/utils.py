"""Shared utility functions for cell-value handling, column management,
and worksheet manipulation."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


# ── Cell-value conversion ──────────────────────────────────────

def safe_float(val: Any) -> float | None:
    """Convert a cell value to float, returning None on failure."""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            val = val.strip()
            if val == "" or val == "-":
                return None
        return float(val)
    except (ValueError, TypeError):
        return None


def safe_str(val: Any) -> str:
    """Convert cell to stripped string.  NaN / None → ''."""
    if val is None:
        return ""
    if isinstance(val, float) and np.isnan(val):
        return ""
    return str(val).strip()


# ── Column lookup ──────────────────────────────────────────────

def find_col_by_keyword(headers: list[str], keyword: str) -> int | None:
    """Find 1-based column index by keyword substring match.

    Returns None if not found; warns and returns first match if ambiguous.
    """
    matches = [i for i, h in enumerate(headers) if keyword in safe_str(h)]
    if len(matches) == 1:
        return matches[0] + 1  # 1-based
    if len(matches) == 0:
        return None
    # Prefer exact match
    exact = [i for i in matches if safe_str(headers[i]) == keyword]
    if len(exact) == 1:
        return exact[0] + 1
    log.warning(
        "Multiple columns match '%s': %s, using first",
        keyword, [headers[i] for i in matches],
    )
    return matches[0] + 1


def get_headers(ws) -> list[str]:
    """Get cleaned header values from row 1 of a worksheet."""
    return [
        safe_str(ws.cell(row=1, column=c).value)
        for c in range(1, ws.max_column + 1)
    ]


# ── Column insertion / deletion ────────────────────────────────

def insert_column(ws, after_col_1based: int, header: str = "") -> int:
    """Insert a column after the given 1-based column index.

    Returns the new column's 1-based index.
    """
    ws.insert_cols(after_col_1based + 1)
    ws.cell(row=1, column=after_col_1based + 1, value=header)
    return after_col_1based + 1


def delete_column(ws, col_1based: int) -> None:
    """Delete a column by 1-based index."""
    ws.delete_cols(col_1based)


# ── Text cleanup ───────────────────────────────────────────────

def remove_all_spaces(ws) -> None:
    """Step 0: Remove all ASCII + full-width spaces from text cells."""
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=ws.max_column):
        for cell in row:
            if isinstance(cell.value, str):
                cell.value = cell.value.replace(" ", "").replace("　", "")
    log.info("Step 0: Removed all spaces from text cells.")


# ── Sequence column ────────────────────────────────────────────

def add_sequence_column(ws) -> None:
    """Step 2: Add 序号 column at far left, fill 1, 2, 3, ..."""
    ws.insert_cols(1)
    ws.cell(row=1, column=1, value="序号")
    for r in range(2, ws.max_row + 1):
        ws.cell(row=r, column=1, value=r - 1)
    log.info("Step 2: Added 序号 column, filled 1..%d.", ws.max_row - 1)


