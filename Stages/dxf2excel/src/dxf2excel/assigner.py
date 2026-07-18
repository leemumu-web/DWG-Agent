"""TEXT-to-cell assignment by coordinate containment.

Places each TEXT entity into the correct grid cell based on its X,Y position,
with tolerance for Y-offsets (e.g. total_weight values at Y + 0.08).
"""

from __future__ import annotations

from loguru import logger

from .config import TEXT_INSET_TOLERANCE
from .models import GridCell, TextEntity


def assign_texts_to_cells(
    cells: list[list[GridCell]],
    texts: list[TextEntity],
    margin: float = TEXT_INSET_TOLERANCE,
) -> tuple[list[list[GridCell]], int]:
    """For each TEXT, find the cell it belongs to by coordinate containment.

    Args:
        cells: 2D grid of cells (cells[row][col]).
        texts: TEXT entities to assign.
        margin: Horizontal/vertical tolerance for cell boundaries.

    Returns:
        (cells with texts populated, orphan_count)
    """
    orphans = 0

    # Pre-compute row Y ranges for fast lookup
    if not cells or not cells[0]:
        return cells, len(texts)

    n_cols = len(cells[0])
    col_x_bounds = [
        (cells[0][j].x_min, cells[0][j].x_max) for j in range(n_cols)
    ]
    row_y_bounds = [
        (cells[i][0].y_min, cells[i][0].y_max) for i in range(len(cells))
    ]

    for text in texts:
        # Find column
        col_idx = _find_col(text.x, col_x_bounds, margin)

        # Find row (with tolerance)
        row_idx = _find_row(text.y, row_y_bounds, margin)

        if col_idx is not None and row_idx is not None:
            cells[row_idx][col_idx].texts.append(text)
        else:
            orphans += 1
            logger.debug(
                f"Orphan TEXT at ({text.x:.1f}, {text.y:.1f}): "
                f"col={col_idx}, row={row_idx}, text={text.text[:30]!r}"
            )

    return cells, orphans


def _find_col(
    x: float,
    col_bounds: list[tuple[float, float]],
    margin: float,
) -> int | None:
    """Find column index where x falls within bounds.

    Priority:
    1. First column where x is within NATURAL bounds (no margin).
    2. First column where x is within bounds ± margin.
    3. Closest column center (within 3× margin).
    """
    # Try natural bounds first (no margin)
    for j, (x_min, x_max) in enumerate(col_bounds):
        if x_min <= x <= x_max:
            return j

    # Try extended bounds (with margin)
    for j, (x_min, x_max) in enumerate(col_bounds):
        if x_min - margin <= x <= x_max + margin:
            return j

    # Fall back to closest center
    best = None
    best_dist = float("inf")
    for j, (x_min, x_max) in enumerate(col_bounds):
        center = (x_min + x_max) / 2
        dist = abs(x - center)
        if dist < best_dist:
            best_dist = dist
            best = j

    if best_dist <= margin * 3:
        return best
    return None


def _find_row(
    y: float,
    row_bounds: list[tuple[float, float]],
    margin: float,
) -> int | None:
    """Find row index where y falls within bounds.

    Uses midpoint containment: text.y should be between y_min and y_max.
    CAD Y increases upward, row 0 = top row.

    With margin, handles Y-offset cases like total_weight at Y+0.08.
    """
    best = None
    best_dist = float("inf")

    for i, (y_min, y_max) in enumerate(row_bounds):
        if y_min - margin <= y <= y_max + margin:
            return i
        center = (y_min + y_max) / 2
        dist = abs(y - center)
        if dist < best_dist:
            best_dist = dist
            best = i

    if best_dist <= margin * 3:
        return best
    return None


def merge_cell_texts(cells: list[list[GridCell]]) -> None:
    """Post-process: merge multiple TEXT entities in each cell.

    Joins text values with a space, removes duplicates.
    For cells with text at different Y offsets within the same cell,
    keeps the first text (typically the main value).
    """
    for row in cells:
        for cell in row:
            if not cell.texts:
                cell.merged_text = ""
                continue

            # Deduplicate by text content (keeping first occurrence)
            seen: set[str] = set()
            unique_texts = []
            for t in cell.texts:
                stripped = t.text.strip()
                if stripped and stripped not in seen:
                    seen.add(stripped)
                    unique_texts.append(stripped)

            cell.merged_text = " ".join(unique_texts)
