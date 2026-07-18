"""Row classification — identifies header, component_summary, data, fastener,
summary, and total rows.  Also performs downward fill of component_no
for rows that inherit it from a preceding component_summary row.

New row types (v2):
  subheader           — drawing name row (always first row)
  header              — column title row (≥3 header keywords)
  component_summary   — 构件号 row: col 0 filled, col 1-2 empty, totals present
  data                — standard part row: part_no and spec non-empty
  fastener_data       — fastener row: spec matches M/STUD/NUT/D patterns, no part_no
  summary             — sub-total row
  total               — grand total row (last data-like row or 合计)
"""

from __future__ import annotations

import re

from .config import (
    FASTENER_MATERIALS,
    FASTENER_SPEC_PATTERNS,
    HEADER_KEYWORDS,
    RowType,
    SUMMARY_KEYWORDS,
    TOTAL_ROW_PATTERNS,
)
from .models import GridRow


def classify_rows(grid_rows: list[GridRow]) -> list[GridRow]:
    """Classify each row and perform component_no downward fill.

    Two-pass algorithm:
    1. Classify each row independently.
    2. Propagate component_no from component_summary rows to
       subsequent data/fastener rows.
    """
    n = len(grid_rows)

    # Pass 1: independent classification
    for i, row in enumerate(grid_rows):
        cells_text = [c.merged_text for c in row.cells]
        row.row_type, row.confidence = _classify_single_row(
            cells_text, row_index=i, total_rows=n
        )

    # Pass 2: positional corrections + downward fill
    current_component: str | None = None

    for i, row in enumerate(grid_rows):
        cells_text = [c.merged_text for c in row.cells]

        # If this is a component_summary row, capture its component_no
        if row.row_type == RowType.COMPONENT_SUMMARY:
            current_component = _extract_component_no(cells_text)

        # If this is a data/fastener row with empty col 0, inherit component_no
        if row.row_type in (RowType.DATA, RowType.FASTENER_DATA):
            col0 = cells_text[0].strip() if cells_text else ""
            if not col0 and current_component:
                # Mark it as having inherited component
                pass  # handled in pipeline via component_no field

        # Re-classify last data-like row as TOTAL if appropriate
        if i == n - 1 and row.row_type in (RowType.DATA, RowType.FASTENER_DATA):
            row.row_type, row.confidence = _classify_as_potential_total(
                cells_text, i, n
            )

    return grid_rows


def _classify_single_row(
    cells: list[str],
    row_index: int,
    total_rows: int,
) -> tuple[RowType, float]:
    """Classify a single row by its cell content."""
    combined = " ".join(c.strip() for c in cells if c.strip())
    col0 = cells[0].strip() if len(cells) > 0 else ""
    col1 = cells[1].strip() if len(cells) > 1 else ""
    col2 = cells[2].strip() if len(cells) > 2 else ""

    # 1. Header: contains multiple known column header keywords
    header_hits = sum(1 for kw in HEADER_KEYWORDS if kw in combined)
    if header_hits >= 3:
        return RowType.HEADER, min(1.0, header_hits / 5.0)

    # 2. Subheader: first row, drawing name pattern
    if row_index == 0:
        if col0 and _looks_like_drawing_name(col0):
            return RowType.SUBHEADER, 0.9
        for c in cells:
            if any(kw in c for kw in SUMMARY_KEYWORDS):
                return RowType.SUMMARY, 0.85
        # If first row has content but isn't a drawing name or header,
        # it might be a component_summary
        if col0 and not col1 and not col2:
            return RowType.COMPONENT_SUMMARY, 0.8

    # 3. Component summary: col 0 filled, col 1-2 empty, totals present
    if col0 and not col1 and not col2:
        # Check if later columns have numeric totals
        has_totals = _has_numeric_in_later_cols(cells, start_col=4)
        if has_totals:
            return RowType.COMPONENT_SUMMARY, 0.85
        # Even without totals, if col 0 looks like a component name
        if _looks_like_component_name(col0):
            return RowType.COMPONENT_SUMMARY, 0.75

    # 4. Total row: first cell contains total keywords
    if any(kw in col0 for kw in TOTAL_ROW_PATTERNS):
        return RowType.TOTAL, 0.9
    if any(kw in col0 for kw in SUMMARY_KEYWORDS):
        return RowType.SUMMARY, 0.85

    # 5. Check for data rows (≥3 non-empty cells)
    non_empty = sum(1 for c in cells if c.strip())
    if non_empty >= 3:
        # Check if it's a fastener row
        if _is_fastener_row(cells):
            return RowType.FASTENER_DATA, 0.8
        return RowType.DATA, 0.8

    # 6. Empty row
    if non_empty == 0:
        return RowType.EMPTY, 1.0

    return RowType.UNKNOWN, 0.3


def _is_fastener_row(cells: list[str]) -> bool:
    """Check if a row is a fastener/bolt row.

    Indicators:
    - spec (col 2 or nearby) matches M/STUD/NUT/D patterns
    - material (col 4 or nearby) is C, STUD, TS10.9, TS8.8
    - part_no (col 0 or 1) is empty
    - No weights or area
    """
    # Find spec column — typically col 2 in 10-col, col 1 in 9-col
    spec_candidates = []
    mat_candidates = []
    for i, c in enumerate(cells):
        txt = c.strip()
        if not txt:
            continue
        # Check fastener spec patterns
        for pat in FASTENER_SPEC_PATTERNS:
            if re.match(pat, txt):
                spec_candidates.append(i)
                break
        # Check fastener materials
        if txt.upper() in FASTENER_MATERIALS:
            mat_candidates.append(i)

    if spec_candidates and mat_candidates:
        return True
    if spec_candidates and len(spec_candidates) >= 1:
        # Also check: no numeric in weight/area columns (cols 5-7)
        for j in (5, 6, 7):
            if j < len(cells) and cells[j].strip():
                try:
                    float(cells[j].strip())
                    return False  # has weights — not a bare fastener
                except ValueError:
                    pass
        return True

    return False


def _has_numeric_in_later_cols(cells: list[str], start_col: int) -> bool:
    """Check if any cell from start_col onward contains a number."""
    for i in range(start_col, len(cells)):
        txt = cells[i].strip()
        if txt:
            try:
                float(txt)
                return True
            except ValueError:
                pass
    return False


def _looks_like_component_name(text: str) -> bool:
    """Check if text looks like a component/member name.

    Examples: SKG-D-4GZ-7, SKG-C-WGKL-29
    """
    parts = text.replace("@", "-").split("-")
    alpha_segments = sum(1 for p in parts if p and any(c.isalpha() for c in p))
    return alpha_segments >= 2 and len(text) >= 8


def _extract_component_no(cells: list[str]) -> str | None:
    """Extract component number from a component_summary row's col 0."""
    if cells and cells[0].strip():
        return cells[0].strip()
    return None


def _classify_as_potential_total(
    cells: list[str],
    row_index: int,
    total_rows: int,
) -> tuple[RowType, float]:
    """Re-classify a row that might be a total row."""
    if len(cells) >= 7:
        col0 = cells[0].strip() if len(cells) > 0 else ""
        total_wt = cells[6].strip() if len(cells) > 6 else ""
        if (not col0 or any(kw in col0 for kw in TOTAL_ROW_PATTERNS)) and total_wt:
            try:
                float(total_wt)
                return RowType.TOTAL, 0.75
            except ValueError:
                pass
    return RowType.DATA, 0.8


def _looks_like_drawing_name(text: str) -> bool:
    """Check if text looks like a drawing name (e.g. 'B7-B1-A1-GGZ-1')."""
    parts = text.replace("@", "-").split("-")
    alpha_num = sum(1 for p in parts if p and any(c.isalpha() for c in p))
    return alpha_num >= 2
