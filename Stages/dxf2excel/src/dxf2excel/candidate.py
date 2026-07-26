"""Table candidate identification and scoring.

Scores each anonymous block in a DXF file for table-likeness
based on TEXT/LINE ratio, grid regularity, coordinate repetition,
and structural indicators.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from loguru import logger

from .config import (
    DrawingType,
    GRID_COL_COUNT_MAX,
    GRID_COL_COUNT_MIN,
    SCORE_ENTITY_MIN,
    SCORE_LINE_MIN,
    SCORE_TEXT_MIN,
    X_CLUSTER_TOLERANCE_FLOOR,
    Y_CLUSTER_TOLERANCE_FLOOR,
)
from .models import LineEntity, TextEntity


def _adaptive_tolerance(
    lines: list[LineEntity],
    texts: list[TextEntity],
) -> tuple[float, float]:
    """Compute adaptive clustering tolerances from text height or line lengths.

    Returns (y_tolerance, x_tolerance).
    Falls back to floor values when no heuristics are available.
    """
    # Try text height first
    heights = [t.height for t in texts if t.height > 0]
    if heights:
        heights.sort()
        median_h = heights[len(heights) // 2]
        tol = max(Y_CLUSTER_TOLERANCE_FLOOR, median_h * 0.1)
        return tol, tol

    # Try horizontal line lengths (row height ≈ typical horizontal segment)
    h_lengths: list[float] = []
    for ln in lines:
        dx = abs(ln.x2 - ln.x1)
        dy = abs(ln.y2 - ln.y1)
        if dy < 0.1 and 1.0 < dx < 200:
            h_lengths.append(dx)
    if h_lengths:
        h_lengths.sort()
        median_len = h_lengths[len(h_lengths) // 2]
        tol = max(Y_CLUSTER_TOLERANCE_FLOOR, median_len * 0.05)
        return tol, tol

    return Y_CLUSTER_TOLERANCE_FLOOR, X_CLUSTER_TOLERANCE_FLOOR


def _compute_grid_regularity(
    lines: list[LineEntity],
) -> tuple[float, int, int]:
    """Score how well LINE endpoints form a regular grid.

    Extracts X from vertical segments and Y from horizontal segments,
    clusters them, and scores based on:
    - Column count in [GRID_COL_COUNT_MIN, GRID_COL_COUNT_MAX]
    - Cluster separation quality (inter-gap / intra-spread)
    - Row count >= 3

    Returns (regularity_score, estimated_cols, estimated_rows).
    Score is 0.0-1.0.  Higher = more grid-like.
    """
    # Classify lines
    v_x_vals: list[float] = []
    h_y_vals: list[float] = []

    for ln in lines:
        dx = abs(ln.x2 - ln.x1)
        dy = abs(ln.y2 - ln.y1)
        if dy < 0.1 and dx > 0.5:  # horizontal
            h_y_vals.append(ln.y1)
        elif dx < 0.1 and dy > 0.5:  # vertical
            v_x_vals.append(ln.x1)

    if len(v_x_vals) < 6 or len(h_y_vals) < 4:
        return 0.0, 0, 0

    # Adaptive tolerance from line endpoint spread
    v_x_vals.sort()
    x_span = v_x_vals[-1] - v_x_vals[0] if v_x_vals else 1.0
    x_tol = max(0.1, x_span * 0.005)  # 0.5% of X span

    h_y_vals.sort()
    y_span = h_y_vals[-1] - h_y_vals[0] if h_y_vals else 1.0
    y_tol = max(0.1, y_span * 0.005)

    # Cluster X values
    x_clusters = _simple_cluster(v_x_vals, x_tol)
    y_clusters = _simple_cluster(h_y_vals, y_tol)

    n_cols = len(x_clusters)
    n_rows = len(y_clusters)

    # --- Column count score ---
    if GRID_COL_COUNT_MIN <= n_cols <= GRID_COL_COUNT_MAX:
        col_score = 1.0
    elif n_cols < GRID_COL_COUNT_MIN:
        col_score = n_cols / GRID_COL_COUNT_MIN
    else:
        col_score = max(0.0, 1.0 - (n_cols - GRID_COL_COUNT_MAX) / 10.0)

    # --- Row count score ---
    if n_rows >= 3:
        row_score = min(1.0, n_rows / 10.0)
    else:
        row_score = n_rows / 3.0

    # --- Cluster separation quality ---
    # Ratio of inter-cluster gap to intra-cluster spread
    separation = _cluster_separation_score(x_clusters)

    # --- Combined ---
    score = 0.4 * col_score + 0.3 * row_score + 0.3 * separation
    return round(min(1.0, max(0.0, score)), 4), n_cols, n_rows


def _simple_cluster(values: list[float], tolerance: float) -> list[list[float]]:
    """Greedy 1D clustering."""
    if not values:
        return []
    clusters: list[list[float]] = [[values[0]]]
    for v in values[1:]:
        if v - clusters[-1][-1] <= tolerance:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    # Filter tiny clusters (likely noise)
    return [c for c in clusters if len(c) >= 2 or len(clusters) <= 20]


def _cluster_separation_score(clusters: list[list[float]]) -> float:
    """Score cluster quality: high inter-cluster gap / low intra-cluster spread."""
    if len(clusters) < 2:
        return 0.0

    centroids = [sum(c) / len(c) for c in clusters]
    spreads = [max(c) - min(c) for c in clusters if len(c) > 1]

    if not spreads or max(spreads) == 0:
        return 0.5

    gaps = [centroids[i + 1] - centroids[i] for i in range(len(centroids) - 1)]
    if not gaps:
        return 0.5

    avg_spread = sum(spreads) / len(spreads)
    avg_gap = sum(gaps) / len(gaps)

    if avg_spread == 0:
        return 0.8

    ratio = avg_gap / avg_spread
    # Good separation: gap >> spread.  ratio > 2 → score near 1.0
    return min(1.0, ratio / 3.0)


def compute_block_stats(
    block_name: str,
    texts: list[TextEntity],
    lines: list[LineEntity],
) -> dict[str, Any]:
    """Compute per-block statistics for candidate scoring."""
    text_count = len(texts)
    line_count = len(lines)

    all_x: list[float] = []
    all_y: list[float] = []
    text_ys: list[float] = []

    for t in texts:
        all_x.append(t.x)
        all_y.append(t.y)
        text_ys.append(t.y)
    for ln in lines:
        all_x.extend([ln.x1, ln.x2])
        all_y.extend([ln.y1, ln.y2])

    bbox_x1 = min(all_x) if all_x else 0.0
    bbox_y1 = min(all_y) if all_y else 0.0
    bbox_x2 = max(all_x) if all_x else 0.0
    bbox_y2 = max(all_y) if all_y else 0.0

    h_count = 0
    v_count = 0
    for ln in lines:
        dx = abs(ln.x2 - ln.x1)
        dy = abs(ln.y2 - ln.y1)
        if dy < 0.1 and dx > 0.1:
            h_count += 1
        elif dx < 0.1 and dy > 0.1:
            v_count += 1

    y_rounded = [round(y * 2) / 2 for y in text_ys]
    unique_y = len(set(y_rounded))

    # Grid regularity
    grid_reg, est_cols, est_rows = _compute_grid_regularity(lines)

    # Adaptive tolerances
    y_tol, x_tol = _adaptive_tolerance(lines, texts)

    layer_counts = dict(Counter(t.layer for t in texts))

    return {
        "block_name": block_name,
        "text_count": text_count,
        "line_count": line_count,
        "entity_total": text_count + line_count,
        "bbox_x1": bbox_x1,
        "bbox_y1": bbox_y1,
        "bbox_x2": bbox_x2,
        "bbox_y2": bbox_y2,
        "h_line_count": h_count,
        "v_line_count": v_count,
        "layer_counts": layer_counts,
        "unique_y_count": unique_y,
        "text_y_values": text_ys,
        "candidate_score": 0.0,
        "grid_regularity": grid_reg,
        "estimated_cols": est_cols,
        "estimated_rows": est_rows,
        "y_tolerance": y_tol,
        "x_tolerance": x_tol,
    }


def score_candidate(stats: dict[str, Any]) -> float:
    """Score a block for table-like characteristics.

    Hard filters (any fail → score 0.0):
      - text_count >= SCORE_TEXT_MIN (10)
      - line_count >= SCORE_LINE_MIN (20)
      - entity_total >= SCORE_ENTITY_MIN (50)
      - T/L ratio must be > 0.05  (excludes structural drawings at ~0.03)

    NOTE: No upper entity cap.  Large tables (up to ~1200 entities)
    are valid material tables.  Structural drawings are excluded by
    the T/L ratio check and grid irregularity scoring.

    Sub-scores (weighted):
      - ratio_score (0.25): TEXT/LINE ratio closeness to [0.3, 1.0]
      - grid_regularity (0.30): LINE endpoint clustering quality
      - y_repeat_score (0.20): Y-coordinate repetition (row evidence)
      - hv_balance (0.15): horizontal vs vertical line balance
      - aspect_score (0.10): bounding box aspect ratio
    """
    tc = stats["text_count"]
    lc = stats["line_count"]
    total = stats["entity_total"]

    # --- Hard filters ---
    if tc < SCORE_TEXT_MIN:
        return 0.0
    if lc < SCORE_LINE_MIN:
        return 0.0
    if total < SCORE_ENTITY_MIN:
        return 0.0

    # T/L ratio: structural blocks have ~0.03, tables have 0.4-0.8
    if lc > 0:
        ratio = tc / lc
        if ratio < 0.05:  # structural drawing
            return 0.0
    else:
        return 0.0

    # --- Ratio score ---
    if 0.3 <= ratio <= 1.0:
        ratio_score = 1.0
    elif ratio < 0.3:
        ratio_score = ratio / 0.3
    else:
        ratio_score = 1.0 / ratio
    ratio_score = max(0.0, min(1.0, ratio_score))

    # --- Grid regularity score ---
    grid_reg = stats.get("grid_regularity", 0.0)

    # --- Y-repeat score ---
    unique_y = stats["unique_y_count"]
    y_repeat_score = 1.0 - (unique_y / max(tc, 1))
    y_repeat_score = max(0.0, min(1.0, y_repeat_score))

    # --- H/V balance ---
    hc = stats["h_line_count"]
    vc = stats["v_line_count"]
    if hc > 0 and vc > 0:
        hv_ratio = hc / vc
        hv_score = min(hv_ratio, 1.0 / hv_ratio) if hv_ratio > 0 else 0.0
    else:
        hv_score = 0.0

    # --- Aspect ratio ---
    width = stats["bbox_x2"] - stats["bbox_x1"]
    height = stats["bbox_y2"] - stats["bbox_y1"]
    if height > 0:
        aspect = width / height
        if 1.0 <= aspect <= 6.0:
            aspect_score = 1.0
        elif aspect < 1.0:
            aspect_score = aspect
        else:
            aspect_score = max(0.0, 6.0 / aspect)
    else:
        aspect_score = 0.0

    # --- Weighted average ---
    score = (
        0.25 * ratio_score
        + 0.30 * grid_reg
        + 0.20 * y_repeat_score
        + 0.15 * hv_score
        + 0.10 * aspect_score
    )

    return round(min(1.0, max(0.0, score)), 4)


def identify_table_blocks(
    block_data: dict[str, tuple[list[TextEntity], list[LineEntity]]],
    threshold: float = 0.3,
) -> list[tuple[str, dict[str, Any]]]:
    """Score all blocks and return those above threshold, sorted by score."""
    candidates: list[tuple[str, dict[str, Any]]] = []

    for block_name, (texts, lines) in block_data.items():
        stats = compute_block_stats(block_name, texts, lines)
        score = score_candidate(stats)
        stats["candidate_score"] = score

        if score >= threshold:
            candidates.append((block_name, stats))
            logger.info(
                f"  {block_name}: score={score:.3f}  grid_reg={stats['grid_regularity']:.3f}  "
                f"TEXT={stats['text_count']}  LINE={stats['line_count']}  "
                f"est_cols={stats['estimated_cols']}  "
                f"bbox=({stats['bbox_x1']:.0f},{stats['bbox_y1']:.0f})-"
                f"({stats['bbox_x2']:.0f},{stats['bbox_y2']:.0f})"
            )

    candidates.sort(key=lambda x: x[1]["candidate_score"], reverse=True)
    return candidates


def detect_drawing_type(filename: str) -> DrawingType:
    """Detect the drawing type from the filename.

    Patterns:
      BYSJ-B7-B1-{GGZ|MSZJ}-###@...  → GGZ / MSZJ
      BYSH-SKG-...                    → SKG
    """
    name_upper = filename.upper()
    if "SKG" in name_upper:
        return DrawingType.SKG
    if "MSZJ" in name_upper:
        return DrawingType.MSZJ
    if "GGZ" in name_upper:
        return DrawingType.GGZ
    return DrawingType.UNKNOWN
