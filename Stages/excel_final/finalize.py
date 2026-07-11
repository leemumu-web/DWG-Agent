"""Steps 20-25: Finalize output sheet and verify correctness.

Step 20: Insert 表净重 = 总净重 × 构件数.
Step 21: Verify Σ表净重 == original 总净重 合计.
Step 22: Insert 表毛重 = 总毛重 × 构件数.
Step 23: Verify Σ表毛重 == original 总毛重 合计.
Step 24: Insert 左进, 右进, 下料长度 (blank) between 长度 and 材质.
Step 25: Verify final header sequence, apply unit suffixes.
"""

from __future__ import annotations

import re
import logging

import config as cfg
from utils import safe_str, safe_float, get_headers, find_col_by_keyword, insert_column, delete_column

log = logging.getLogger(__name__)


def steps_20_24_finalize(wb, ws_name, total_col, theo_total_wt_col):
    """Run steps 20-25 on sheet *ws_name*."""
    ws = wb[ws_name]

    # Clean up: delete 拆分标记 column
    headers = get_headers(ws)
    marker_col = find_col_by_keyword(headers, "拆分标记")
    if marker_col:
        delete_column(ws, marker_col)
        log.info("Deleted '拆分标记' column before final verification.")

    headers = get_headers(ws)

    net_total_col = find_col_by_keyword(headers, cfg.KW_总净重)
    gross_unit_col = find_col_by_keyword(headers, cfg.KW_单毛重)
    gross_total_col = find_col_by_keyword(headers, cfg.KW_总毛重)
    surface_unit_col = find_col_by_keyword(headers, cfg.KW_单表面积) or 0
    comp_qty_col = find_col_by_keyword(headers, "构件数")
    len_col = find_col_by_keyword(headers, cfg.KW_长度)
    mat_col = find_col_by_keyword(headers, cfg.KW_材质)

    # ---- Step 20: 表净重 before 单毛重 ----
    insert_column(ws, gross_unit_col - 1, "表净重")
    table_net_col = gross_unit_col
    gross_unit_col += 1; gross_total_col += 1; surface_unit_col += 1

    for r in range(2, ws.max_row + 1):
        net_total = safe_float(ws.cell(row=r, column=net_total_col).value)
        comp_qty = safe_float(ws.cell(row=r, column=comp_qty_col).value)
        if net_total is not None and comp_qty is not None:
            ws.cell(row=r, column=table_net_col).value = round(net_total * comp_qty, 2)
    log.info("Step 20: Calculated 表净重 = 总净重 × 构件数.")

    # ---- Step 21: Verify 表净重 ----
    table_net_sum = sum(
        safe_float(ws.cell(row=r, column=table_net_col).value) or 0
        for r in range(2, ws.max_row + 1)
    )
    ws_comp = wb["构件表"]
    comp_headers = get_headers(ws_comp)
    comp_net_total_col = find_col_by_keyword(comp_headers, cfg.KW_总净重)
    original_net = None
    if comp_net_total_col:
        comp_id_col = find_col_by_keyword(comp_headers, cfg.KW_构件编号) or 1
        for r in range(ws_comp.max_row, 1, -1):
            if "合计" in safe_str(ws_comp.cell(row=r, column=comp_id_col).value):
                original_net = safe_float(ws_comp.cell(row=r, column=comp_net_total_col).value)
                break
    if original_net is not None:
        diff = abs(table_net_sum - original_net)
        if diff < 1.0:
            log.info("Step 21 ✓: 表净重 sum = %.2f ≈ 原始合计 %.2f (diff=%.4f).",
                     table_net_sum, original_net, diff)
        else:
            log.error("Step 21 ✗: 表净重 sum = %.2f ≠ 原始合计 %.2f (diff=%.2f)!",
                      table_net_sum, original_net, diff)
    else:
        log.warning("Step 21: Cannot find original total for verification.")

    # ---- Step 22: 表毛重 before 单表面积 ----
    insert_column(ws, surface_unit_col - 1, "表毛重")
    table_gross_col = surface_unit_col
    surface_unit_col += 1

    for r in range(2, ws.max_row + 1):
        gross_total = safe_float(ws.cell(row=r, column=gross_total_col).value)
        comp_qty = safe_float(ws.cell(row=r, column=comp_qty_col).value)
        if gross_total is not None and comp_qty is not None:
            ws.cell(row=r, column=table_gross_col).value = round(gross_total * comp_qty, 2)
    log.info("Step 22: Calculated 表毛重 = 总毛重 × 构件数.")

    # ---- Step 23: Verify 表毛重 ----
    table_gross_sum = sum(
        safe_float(ws.cell(row=r, column=table_gross_col).value) or 0
        for r in range(2, ws.max_row + 1)
    )
    comp_gross_total_col = find_col_by_keyword(comp_headers, cfg.KW_总毛重)
    original_gross = None
    if comp_gross_total_col:
        comp_id_col = find_col_by_keyword(comp_headers, cfg.KW_构件编号) or 1
        for r in range(ws_comp.max_row, 1, -1):
            if "合计" in safe_str(ws_comp.cell(row=r, column=comp_id_col).value):
                original_gross = safe_float(ws_comp.cell(row=r, column=comp_gross_total_col).value)
                break
    if original_gross is not None:
        diff = abs(table_gross_sum - original_gross)
        if diff < 1.0:
            log.info("Step 23 ✓: 表毛重 sum = %.2f ≈ 原始合计 %.2f (diff=%.4f).",
                     table_gross_sum, original_gross, diff)
        else:
            log.error("Step 23 ✗: 表毛重 sum = %.2f ≠ 原始合计 %.2f (diff=%.2f)!",
                      table_gross_sum, original_gross, diff)
    else:
        log.warning("Step 23: Cannot find original gross total for verification.")

    # ---- Step 24: 左进, 右进, 下料长度 between 长度 and 材质 ----
    if len_col and mat_col:
        for col_name in ["下料长度", "右进", "左进"]:
            insert_column(ws, len_col, col_name)
    log.info("Step 24: Inserted 左进, 右进, 下料长度 (left blank).")

    # ---- Step 25: Verify final headers & renumber 序号 ----
    seq_col = find_col_by_keyword(headers, "序号")
    if seq_col:
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=seq_col).value = r - 1
        log.info("Renumbered 序号 1..%d.", ws.max_row - 1)

    final_headers = get_headers(ws)
    clean_final = [re.sub(r"\([^)]*\)", "", h).strip() for h in final_headers]
    log.info("Step 25: Final headers (%d cols): %s", len(clean_final), clean_final)

    mismatches = []
    for i, expected in enumerate(cfg.EXPECTED_HEADERS):
        if i < len(clean_final):
            if expected != clean_final[i]:
                mismatches.append(f"  Col {i + 1}: expected '{expected}', got '{clean_final[i]}'")
        else:
            mismatches.append(f"  Col {i + 1}: expected '{expected}', missing")
    if len(clean_final) > len(cfg.EXPECTED_HEADERS):
        for i in range(len(cfg.EXPECTED_HEADERS), len(clean_final)):
            mismatches.append(f"  Col {i + 1}: extra '{clean_final[i]}'")

    if mismatches:
        log.warning("Step 25: Header mismatches (%d):", len(mismatches))
        for mm in mismatches:
            log.warning(mm)
    else:
        log.info("Step 25 ✓: All headers match expected sequence!")

    # Apply unit suffixes
    final_headers = get_headers(ws)
    applied = 0
    for c in range(1, ws.max_column + 1):
        h = safe_str(ws.cell(row=1, column=c).value)
        if h in cfg.HEADER_UNITS:
            ws.cell(row=1, column=c).value = cfg.HEADER_UNITS[h]
            applied += 1
    log.info("Applied unit suffixes to %d header columns.", applied)

    return table_net_col, table_gross_col
