"""Grid recovery from LINE endpoints — the core algorithm.

Recovers table row/column boundaries by clustering endpoint coordinates
from LINE entities.  Uses line-based primary path with TEXT-based fallback.

Tolerances are scale-independent: computed from text height when available,
falling back to fixed values.
"""

from __future__ import annotations

from loguru import logger

from .config import (
    ROW_HEIGHT_MIN,
    ROW_HEIGHT_MIN_RATIO,
    X_CLUSTER_TOLERANCE,
    Y_CLUSTER_TOLERANCE,
    Y_CLUSTER_TOLERANCE_FLOOR,
)
from .models import GridCell, LineEntity, TextEntity


def cluster_1d(
    values: list[float],
    tolerance: float,
) -> list[tuple[float, list[float]]]:
    """Greedy 1D clustering of sorted values.

    Args:
        values: List of float values to cluster.
        tolerance: Maximum gap between consecutive values in same cluster.

    Returns:
        List of (centroid, members) sorted by centroid ascending.
    """
    if not values:
        return []

    sorted_vals = sorted(values)
    clusters: list[list[float]] = []
    current: list[float] = [sorted_vals[0]]

    for v in sorted_vals[1:]:
        if v - current[-1] <= tolerance:
            current.append(v)
        else:
            clusters.append(current)
            current = [v]
    clusters.append(current)

    return [(sum(c) / len(c), c) for c in clusters]


def classify_line(x1: float, y1: float, x2: float, y2: float) -> str:
    """Classify a LINE as horizontal (H), vertical (V), or diagonal (D).

    0.1 是 H/V/D 分类的方向阈值：CAD 端点噪声量级为 0.01-0.5 单位，
    两端在 0.1 内视为「无位移」；同时要求另一轴位移 > 0.1 排除点线。
    改动会直接影响网格恢复，需与 candidate 的水平线判定（dy<0.1 且
    dx>0.5）保持口径一致。
    """
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)
    if dy < 0.1 and dx > 0.1:
        return "H"
    elif dx < 0.1 and dy > 0.1:
        return "V"
    else:
        return "D"


def recover_grid(
    lines: list[LineEntity],
    texts: list[TextEntity],
    y_tolerance: float | None = None,
    x_tolerance: float | None = None,
) -> tuple[list[float], list[float], list[LineEntity], list[LineEntity]]:
    """Recover table grid from LINE entities.

    Steps:
    1. Classify lines as H, V, or D.
    2. Cluster H-line Y values → row boundaries.
    3. Cluster V-line X values → column boundaries.
    4. Merge very close row boundaries (adaptive threshold).
    5. If grid recovery fails, fall back to TEXT-based recovery.

    Args:
        lines: All LINE entities.
        texts: TEXT entities (for fallback + adaptive tolerance).
        y_tolerance: Override Y clustering tolerance (None → auto-compute).
        x_tolerance: Override X clustering tolerance (None → auto-compute).

    Returns:
        (row_ys, col_xs, h_lines, v_lines)
        row_ys sorted ascending (CAD coords).
        col_xs sorted ascending.
    """
    # Compute adaptive tolerances if not provided
    if y_tolerance is None or x_tolerance is None:
        atol_y, atol_x = adaptive_grid_tolerance(texts)
        if y_tolerance is None:
            y_tolerance = atol_y
        if x_tolerance is None:
            x_tolerance = atol_x

    # Compute adaptive min row height
    row_height_min = _adaptive_row_height_min(texts, lines)

    # Classify
    h_lines: list[LineEntity] = []
    v_lines: list[LineEntity] = []

    for ln in lines:
        cls = classify_line(ln.x1, ln.y1, ln.x2, ln.y2)
        if cls == "H":
            h_lines.append(ln)
        elif cls == "V":
            v_lines.append(ln)

    # --- Row boundaries from horizontal lines ---
    h_y_vals: list[float] = []
    for ln in h_lines:
        h_y_vals.append(ln.y1)

    row_clusters = cluster_1d(h_y_vals, y_tolerance)
    row_ys = [centroid for centroid, _ in row_clusters]
    row_ys.sort()

    # Merge very close rows
    row_ys = _merge_close_boundaries(row_ys, row_height_min)

    # --- Column boundaries from vertical lines ---
    v_x_vals: list[float] = []
    for ln in v_lines:
        v_x_vals.append(ln.x1)  # x1 ≈ x2 for vertical lines

    col_clusters = cluster_1d(v_x_vals, x_tolerance)
    col_xs = [centroid for centroid, _ in col_clusters]
    col_xs.sort()

    # --- Fallback if grid is poor ---
    # 0.3 是网格质量分门槛、[8,12] 是列数带宽（9-10 列材料表实测范围）：
    # 任一不满足即整体回退 TEXT 聚类。两套评分口径不同——_compute_grid_score_quick
    # 按 10 个边界计分，compute_grid_score 按 9 个数据列计分——勿混用。
    grid_score = _compute_grid_score_quick(row_ys, col_xs, len(texts))
    if grid_score < 0.3:
        logger.warning(
            f"LINE-based grid score low ({grid_score:.2f}), falling back to TEXT-based"
        )
        row_ys, col_xs = recover_grid_from_text(texts)
    elif len(col_xs) < 8 or len(col_xs) > 12:
        logger.warning(
            f"Column count ({len(col_xs)}) outside [8,12], falling back to TEXT-based"
        )
        row_ys, col_xs = recover_grid_from_text(texts)

    return row_ys, col_xs, h_lines, v_lines


def _merge_close_boundaries(
    boundaries: list[float],
    min_gap: float,
) -> list[float]:
    """Merge adjacent boundaries whose gap < min_gap.

    Keeps the average of merged values.
    """
    if len(boundaries) < 2:
        return boundaries

    merged: list[float] = []
    current_group = [boundaries[0]]

    for b in boundaries[1:]:
        if b - current_group[-1] < min_gap:
            current_group.append(b)
        else:
            merged.append(sum(current_group) / len(current_group))
            current_group = [b]
    merged.append(sum(current_group) / len(current_group))

    return merged


def _compute_grid_score_quick(
    row_ys: list[float],
    col_xs: list[float],
    text_count: int,
) -> float:
    """Quick heuristic grid quality score before building cells."""
    n_cols = len(col_xs)
    if n_cols < 2:
        return 0.0

    # Expected 10 boundaries (9 cols + outer edges), allow 8-12
    col_score = max(0.0, 1.0 - abs(n_cols - 10) / 5.0)

    # Row count should be reasonable: at least 3 rows
    n_rows = len(row_ys)
    row_score = min(1.0, n_rows / 3.0) if n_rows >= 2 else 0.0

    return 0.6 * col_score + 0.4 * row_score


def recover_grid_from_text(
    texts: list[TextEntity],
) -> tuple[list[float], list[float]]:
    """Fallback: cluster TEXT coordinates to recover row/column boundaries.

    Creates boundaries at midpoints between text clusters.
    """
    text_ys = sorted({t.y for t in texts})
    text_xs = sorted({t.x for t in texts})

    # Cluster Y coords
    y_clusters = cluster_1d(text_ys, Y_CLUSTER_TOLERANCE)
    y_centers = [c for c, _ in y_clusters]

    # Cluster X coords
    x_clusters = cluster_1d(text_xs, X_CLUSTER_TOLERANCE)
    x_centers = [c for c, _ in x_clusters]

    # Build boundaries at midpoints
    if len(y_centers) >= 2:
        row_ys = []
        y_centers.sort()
        # Outer bounds: half typical row height beyond extreme centers
        half_h = (y_centers[-1] - y_centers[0]) / max(len(y_centers) - 1, 1) / 2
        row_ys.append(y_centers[0] + half_h)  # top boundary
        for i in range(len(y_centers) - 1):
            row_ys.append((y_centers[i] + y_centers[i + 1]) / 2)
        row_ys.append(y_centers[-1] - half_h)  # bottom boundary
    else:
        row_ys = [0.0, 1.0] if y_centers else []

    if len(x_centers) >= 2:
        col_xs = []
        x_centers.sort()
        half_w = (x_centers[-1] - x_centers[0]) / max(len(x_centers) - 1, 1) / 2
        col_xs.append(x_centers[0] - half_w)  # left boundary
        for i in range(len(x_centers) - 1):
            col_xs.append((x_centers[i] + x_centers[i + 1]) / 2)
        col_xs.append(x_centers[-1] + half_w)  # right boundary
    else:
        col_xs = [0.0, 1.0] if x_centers else []

    logger.info(
        f"TEXT-based grid: {len(row_ys)-1} rows × {len(col_xs)-1} cols"
    )
    return row_ys, col_xs


def adaptive_grid_tolerance(
    texts: list[TextEntity],
) -> tuple[float, float]:
    """Compute scale-independent clustering tolerance from text height.

    tolerance = max(floor, median_text_height * 0.1)

    Returns (y_tolerance, x_tolerance) — typically equal.
    """
    heights = [t.height for t in texts if t.height > 0]
    if heights:
        heights.sort()
        median_h = heights[len(heights) // 2]
        tol = max(Y_CLUSTER_TOLERANCE_FLOOR, median_h * 0.1)
        return tol, tol
    return Y_CLUSTER_TOLERANCE, X_CLUSTER_TOLERANCE


def _adaptive_row_height_min(
    texts: list[TextEntity],
    lines: list[LineEntity],
) -> float:
    """Compute adaptive minimum row height from text or line data."""
    # Try text height first
    heights = [t.height for t in texts if t.height > 0]
    if heights:
        heights.sort()
        median_h = heights[len(heights) // 2]
        return max(ROW_HEIGHT_MIN, median_h * ROW_HEIGHT_MIN_RATIO)

    # 水平线长推断分支目前是死代码：h_lengths 收集后从未使用，两分支都
    # 直接返回固定 ROW_HEIGHT_MIN。若需要基于线长的自适应行高，应在此
    # 实现（如 median×系数）；否则应删除该分支，避免误导读者。
    h_lengths: list[float] = []
    for ln in lines:
        dy = abs(ln.y2 - ln.y1)
        if dy < 0.1:
            h_lengths.append(abs(ln.x2 - ln.x1))
    if h_lengths:
        h_lengths.sort()
        # Row height ≈ typical vertical gap ≈ 0.05-0.2 of horizontal span
        return ROW_HEIGHT_MIN

    return ROW_HEIGHT_MIN


def estimate_data_columns(col_xs: list[float]) -> int:
    """Estimate actual data columns by filtering narrow divider columns.

    A column narrower than 15% of median column width is considered
    a divider, not a data column (docstring corrected to match the
    implementation and README — SKG 材料表「数量/单重」之间的窄分隔线实测值).

    Returns estimated number of data columns.
    """
    if len(col_xs) < 3:
        return max(0, len(col_xs) - 1)

    widths = [col_xs[i + 1] - col_xs[i] for i in range(len(col_xs) - 1)]
    widths.sort()
    median_w = widths[len(widths) // 2]
    threshold = median_w * 0.15

    data_cols = sum(1 for w in widths if w >= threshold)
    return data_cols


def build_cells(
    row_ys: list[float],
    col_xs: list[float],
) -> list[list[GridCell]]:
    """Build GridCell objects from row and column boundaries.

    Args:
        row_ys: Y coordinates of row boundaries (sorted ascending).
        col_xs: X coordinates of column boundaries (sorted ascending).

    Returns:
        2D list: cells[row][col].  Row 0 = top row (highest Y).
    """
    n_rows = len(row_ys) - 1
    n_cols = len(col_xs) - 1

    if n_rows < 1 or n_cols < 1:
        logger.warning(f"Invalid grid dimensions: {n_rows}×{n_cols}")
        return []

    cells: list[list[GridCell]] = []
    for i in range(n_rows):
        row_cells: list[GridCell] = []
        # CAD Y increases upward, so row 0 = top = highest Y
        y_top = row_ys[n_rows - i] if n_rows - i < len(row_ys) else row_ys[-1]
        y_bot = row_ys[n_rows - i - 1] if n_rows - i - 1 >= 0 else row_ys[0]
        for j in range(n_cols):
            row_cells.append(
                GridCell(
                    row=i,
                    col=j,
                    x_min=col_xs[j],
                    x_max=col_xs[j + 1],
                    y_min=y_bot,
                    y_max=y_top,
                )
            )
        cells.append(row_cells)

    return cells


def compute_grid_score(
    cells: list[list[GridCell]],
    row_ys: list[float],
    col_xs: list[float],
) -> float:
    """Score grid quality (0.0 to 1.0).

    - Expected column count: 10 boundaries. Score proximity.
    - Row height uniformity: lower std → higher score.
    """
    n_rows = len(row_ys) - 1
    n_cols = len(col_xs) - 1

    if n_cols < 1:
        return 0.0

    # Column count score
    col_score = max(0.0, 1.0 - abs(n_cols - 9) / 5.0)

    # Row height uniformity
    if n_rows >= 2:
        heights = [abs(row_ys[i + 1] - row_ys[i]) for i in range(n_rows)]
        mean_h = sum(heights) / len(heights)
        std_h = (sum((h - mean_h) ** 2 for h in heights) / len(heights)) ** 0.5
        uniformity = max(0.0, 1.0 - std_h / max(mean_h, 0.01))
    else:
        uniformity = 1.0

    return round(0.5 * col_score + 0.5 * uniformity, 4)
