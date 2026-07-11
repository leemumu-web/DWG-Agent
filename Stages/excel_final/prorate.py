"""Weight proration for multi_split rows (BH/I/BT splits).

multi_split copies original weights to both web and flange rows,
doubling the total.  This module prorates based on theoretical
volume to restore correct totals.

Pairs where the flange row has already been cleared by Step 13
(type contains 翼, the VBA flange suffix) are skipped — the web
keeps 100% of the original weight (excel-converter pattern).
"""

from __future__ import annotations

import logging

import config as cfg
from utils import safe_str, safe_float, get_headers, find_col_by_keyword

log = logging.getLogger(__name__)


def step_prorate_split_weights(wb, ws_name):
    """Prorate weight columns for multi_split rows.

    Skips pairs where the flange type contains 翼 (weights
    already cleared by Step 13).
    """
    ws = wb[ws_name]
    headers = get_headers(ws)

    marker_col = find_col_by_keyword(headers, "拆分标记")
    if marker_col is None:
        log.info("Prorate: no 拆分标记 column, skipping.")
        return 0

    type_col = find_col_by_keyword(headers, "类型")
    spec_col = find_col_by_keyword(headers, "规格")
    width_col = find_col_by_keyword(headers, "宽度")
    len_col = find_col_by_keyword(headers, cfg.KW_长度)
    qty_col = find_col_by_keyword(headers, cfg.KW_数量)

    wt_cols = {}
    for kw in [cfg.KW_单净重, cfg.KW_总净重, cfg.KW_单毛重,
                cfg.KW_总毛重, cfg.KW_单表面积, cfg.KW_总表面积]:
        c = find_col_by_keyword(headers, kw)
        if c:
            wt_cols[kw] = c

    last_row = ws.max_row
    prorated = 0
    r = 2
    while r <= last_row:
        marker = safe_str(ws.cell(row=r, column=marker_col).value)
        if marker != "拆":
            r += 1
            continue

        next_marker = safe_str(ws.cell(row=r + 1, column=marker_col).value) if r < last_row else ""
        if next_marker != "拆":
            r += 1
            continue

        # Found a pair: r = web, r+1 = flange
        r_web, r_flange = r, r + 1

        # Step 13 has already cleared weight columns for cover/flange rows.
        # For those pairs the web keeps 100% of the original weight —
        # skip proration to avoid redistributing back to the zeroed flange.
        if type_col:
            flange_type = safe_str(ws.cell(row=r_flange, column=type_col).value)
            if "翼" in flange_type:
                r += 2
                continue

        # Get dimensions
        spec_w = safe_float(ws.cell(row=r_web, column=spec_col).value)
        width_w = safe_float(ws.cell(row=r_web, column=width_col).value)
        spec_f = safe_float(ws.cell(row=r_flange, column=spec_col).value)
        width_f = safe_float(ws.cell(row=r_flange, column=width_col).value)
        len_val = safe_float(ws.cell(row=r_web, column=len_col).value)
        qty_f = safe_float(ws.cell(row=r_flange, column=qty_col).value) or 2

        if None in (spec_w, width_w, spec_f, width_f, len_val):
            r += 2
            continue

        qty_w = safe_float(ws.cell(row=r_web, column=qty_col).value) or 1
        w_theo = spec_w * width_w * len_val * qty_w
        f_theo_total = spec_f * width_f * len_val * qty_f
        total_theo = w_theo + f_theo_total

        if total_theo == 0:
            r += 2
            continue

        w_ratio = w_theo / total_theo
        f_ratio = f_theo_total / total_theo

        for kw, col in wt_cols.items():
            orig_val = safe_float(ws.cell(row=r_web, column=col).value)
            if orig_val is not None:
                ws.cell(row=r_web, column=col).value = round(orig_val * w_ratio, 3)
                ws.cell(row=r_flange, column=col).value = round(orig_val * f_ratio, 3)

        prorated += 1
        r += 2

    log.info("Prorate: adjusted weight columns for %d split pairs.", prorated)
    return prorated
