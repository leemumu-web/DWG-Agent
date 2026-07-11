"""Steps 11-14: Post-split fixes on 整理表_拆板后.

Step 11: (REMOVED — multi_split natively produces "BOX腹"/"BOX翼" labels)
Step 12: (AUTO-SKIP — multi_split with modes=["BH","BT","BOX"] handles all qty×2)
Step 13: Clear weight/area columns for flange rows (翼).
Step 14: Merge 规格=6, 宽度=30 → "6*30".
"""

from __future__ import annotations

import logging

import config as cfg
from utils import safe_str, safe_float, get_headers, find_col_by_keyword

log = logging.getLogger(__name__)


def steps_11_14_post_split(wb, ws_name):
    """Apply steps 11-14 on sheet *ws_name*.  Returns the worksheet."""
    ws = wb[ws_name]
    headers = get_headers(ws)

    type_col = find_col_by_keyword(headers, "类型")
    qty_col = find_col_by_keyword(headers, cfg.KW_数量)
    spec_new_col = find_col_by_keyword(headers, "规格")
    width_col = find_col_by_keyword(headers, "宽度")

    if any(c is None for c in [type_col, qty_col, spec_new_col, width_col]):
        log.warning("Missing required columns for steps 11-14; skipping.")

    # Find 截面型材 column (preserved through multi_split)
    sec_col = None
    for i, h in enumerate(headers):
        if "截面型材" in safe_str(h):
            sec_col = i + 1
            break
    if sec_col is None:
        # Fallback: search for BOX in data
        for c in range(1, ws.max_column + 1):
            for r in range(2, min(20, ws.max_row + 1)):
                if "BOX" in safe_str(ws.cell(row=r, column=c).value).upper():
                    sec_col = c
                    break
            if sec_col:
                break

    # ---- Step 11: (REMOVED) multi_split natively produces "BOX腹"/"BOX翼" labels ----

    # ---- Step 12: BOX腹 → qty × 2 (legacy: only when multi_split didn't split BOX) ----
    if type_col and qty_col:
        # Detect if multi_split already processed this (has 拆分标记 column with "拆")
        marker_col_idx = None
        for i, h in enumerate(headers):
            if "拆分标记" in safe_str(h):
                marker_col_idx = i + 1
                break

        fixed = 0
        for r in range(2, ws.max_row + 1):
            type_val = safe_str(ws.cell(row=r, column=type_col).value)
            if "BOX腹" not in type_val:
                continue
            # If multi_split already split BOX, web qty is already doubled — skip
            if marker_col_idx is not None:
                marker = safe_str(ws.cell(row=r, column=marker_col_idx).value)
                if marker == "拆":
                    continue  # already handled by multi_split
            qty = safe_float(ws.cell(row=r, column=qty_col).value)
            if qty is not None:
                ws.cell(row=r, column=qty_col).value = qty * 2
                fixed += 1
        log.info("Step 12: Multiplied quantity ×2 for %d BOX腹 rows (legacy).", fixed)

    # ---- Step 13: Flange rows (翼) → clear weight/area ----
    # multi_split labels flanges as "翼".
    if type_col:
        cleared = 0
        wt_cols = []
        for kw in [cfg.KW_单净重, cfg.KW_总净重, cfg.KW_单毛重,
                    cfg.KW_总毛重, cfg.KW_单表面积, cfg.KW_总表面积]:
            c = find_col_by_keyword(headers, kw)
            if c:
                wt_cols.append(c)

        for r in range(2, ws.max_row + 1):
            type_val = safe_str(ws.cell(row=r, column=type_col).value)
            if "翼" in type_val:
                for c in wt_cols:
                    ws.cell(row=r, column=c).value = None
                cleared += 1
        log.info("Step 13: Cleared weight/area for %d flange rows.", cleared)

    # ---- Step 14: 规格="6", 宽度="30" → "6*30" ----
    if spec_new_col and width_col:
        fixed = 0
        for r in range(2, ws.max_row + 1):
            if (safe_str(ws.cell(row=r, column=spec_new_col).value) == "6"
                    and safe_str(ws.cell(row=r, column=width_col).value) == "30"):
                ws.cell(row=r, column=spec_new_col).value = "6*30"
                ws.cell(row=r, column=width_col).value = None
                fixed += 1
        log.info("Step 14: Merged 6*30 for %d rows.", fixed)

    return ws
