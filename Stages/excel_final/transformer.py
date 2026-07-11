"""Steps 2-9: Setup columns, split sheets, modify 整理表.

Steps 2-5: Add 序号, 构件数, forward-fill, numerify.
Step 6:    Split into 构件表 + 整理表 (parts only).
Steps 7-9: Delete 构件 columns, insert 类型/规格/宽度, BOX→BH.
"""

from __future__ import annotations

import logging

import config as cfg
from utils import (
    safe_str, safe_float, get_headers, find_col_by_keyword,
    insert_column, delete_column, add_sequence_column,
)
from parser import is_component_start_row, is_component_end_row, is_total_row

log = logging.getLogger(__name__)


# ── Steps 2-5 ──────────────────────────────────────────────────

def steps_2_5_setup(wb, ws):
    """Steps 2-5: Add 序号/构件数, fill component info, numerify.

    Returns (comp_col, comp_qty_col, part_col, qty_col, batch_col, comp_rows)
    — all 1-based.  comp_rows is a list of row numbers for component START rows,
    recorded before forward-fill so Step 6 can use them.
    """
    headers = get_headers(ws)
    log.info("  Current headers: %s", headers)

    batch_col = find_col_by_keyword(headers, cfg.KW_批次) or 0
    comp_col = find_col_by_keyword(headers, cfg.KW_构件编号)
    part_col = find_col_by_keyword(headers, cfg.KW_零件号)
    qty_col = find_col_by_keyword(headers, cfg.KW_数量)

    missing = []
    if comp_col is None: missing.append(cfg.KW_构件编号)
    if part_col is None: missing.append(cfg.KW_零件号)
    if qty_col is None: missing.append(cfg.KW_数量)
    if missing:
        raise ValueError(f"Cannot find required columns: {missing}")

    if batch_col:
        log.info("  Key columns: 批次=%d, 构件编号=%d, 零件号=%d, 数量=%d",
                 batch_col, comp_col, part_col, qty_col)
    else:
        log.info("  Key columns: (no 批次), 构件编号=%d, 零件号=%d, 数量=%d",
                 comp_col, part_col, qty_col)

    # Step 2: Add 序号 at far left
    add_sequence_column(ws)
    if batch_col: batch_col += 1
    comp_col += 1; part_col += 1; qty_col += 1

    # Step 3: Add 构件数 after 构件编号
    insert_column(ws, comp_col, "构件数")
    comp_qty_col = comp_col + 1
    part_col += 1; qty_col += 1
    log.info("Step 3: Inserted 构件数 at column %d.", comp_qty_col)

    # Fill 构件数 from 数量 for component rows (record before forward-fill)
    last_row = ws.max_row
    filled = 0
    comp_rows = []  # record component start row numbers for Step 6
    for r in range(2, last_row + 1):
        if is_component_start_row(ws, r, batch_col, comp_col, part_col) or \
           is_component_end_row(ws, r, part_col):
            ws.cell(row=r, column=comp_qty_col).value = ws.cell(row=r, column=qty_col).value
            filled += 1
        if is_component_start_row(ws, r, batch_col, comp_col, part_col):
            comp_rows.append(r)
    log.info("  Filled 构件数 for %d component rows.", filled)
    # Also fill for total row
    for r in range(2, last_row + 1):
        if is_total_row(ws, r, comp_col):
            ws.cell(row=r, column=comp_qty_col).value = ws.cell(row=r, column=qty_col).value

    # Step 4: Forward-fill 构件编号 and 构件数 for part rows
    current_comp = ""
    current_comp_qty = None
    for r in range(2, last_row + 1):
        if is_component_start_row(ws, r, batch_col, comp_col, part_col):
            current_comp = safe_str(ws.cell(row=r, column=comp_col).value)
            current_comp_qty = ws.cell(row=r, column=comp_qty_col).value
        elif is_component_end_row(ws, r, part_col):
            continue
        elif is_total_row(ws, r, comp_col):
            continue
        else:
            if current_comp:
                ws.cell(row=r, column=comp_col).value = current_comp
            if current_comp_qty is not None:
                ws.cell(row=r, column=comp_qty_col).value = current_comp_qty
    log.info("Step 4: Forward-filled 构件编号 and 构件数 for all part rows.")

    # Step 5: Numerify
    for r in range(2, last_row + 1):
        for col in (comp_col, comp_qty_col):
            val = ws.cell(row=r, column=col).value
            if val is not None and isinstance(val, str) and val.strip():
                try:
                    ws.cell(row=r, column=col).value = float(val)
                except ValueError:
                    pass
    log.info("Step 5: Numerified 构件编号 and 构件数 columns.")

    return comp_col, comp_qty_col, part_col, qty_col, batch_col, comp_rows


# ── Step 6 ─────────────────────────────────────────────────────

def step_6_split_sheets(wb, ws, comp_col, comp_qty_col, part_col, qty_col, batch_col, comp_rows):
    """Step 6: Create 构件表, move component + total rows there.

    Uses pre-recorded *comp_rows* from Step 3 (before forward-fill) so
    that no-batch formats work correctly after 构件编号 has been filled
    into part rows.
    """
    last_row = ws.max_row

    ws_comp = wb.create_sheet("构件表")
    for c in range(1, ws.max_column + 1):
        ws_comp.cell(row=1, column=c, value=ws.cell(row=1, column=c).value)

    # Identify rows to move
    rows_to_move = []
    if batch_col:
        # Standard Tekla: re-detect (safe — batch column distinguishes rows)
        for r in range(2, last_row + 1):
            if is_component_start_row(ws, r, batch_col, comp_col, part_col):
                rows_to_move.append((r, False))
            elif is_component_end_row(ws, r, part_col):
                rows_to_move.append((r, False))
            elif is_total_row(ws, r, comp_col):
                rows_to_move.append((r, True))
    else:
        # No batch: use pre-recorded rows from Step 3 (before forward-fill)
        for r in comp_rows:
            rows_to_move.append((r, False))
        for r in range(2, last_row + 1):
            if is_component_end_row(ws, r, part_col):
                rows_to_move.append((r, False))
            elif is_total_row(ws, r, comp_col):
                rows_to_move.append((r, True))
        seen = set()
        rows_to_move = [(r, t) for r, t in rows_to_move if not (r in seen or seen.add(r))]

    if not rows_to_move:
        log.warning("Step 6: No component rows found to move!")

    # Copy rows to 构件表
    ws_comp_row = 2
    for src_row, _is_total in rows_to_move:
        for c in range(1, ws.max_column + 1):
            ws_comp.cell(row=ws_comp_row, column=c,
                         value=ws.cell(row=src_row, column=c).value)
        ws_comp_row += 1

    # Delete rows from 整理表 (bottom-up)
    for src_row, _ in reversed(rows_to_move):
        ws.delete_rows(src_row)

    # Delete fully blank rows
    remaining = ws.max_row
    blank_rows = []
    for r in range(2, remaining + 1):
        if all(ws.cell(row=r, column=c).value is None for c in range(1, ws.max_column + 1)):
            blank_rows.append(r)
    for r in reversed(blank_rows):
        ws.delete_rows(r)

    log.info("Step 6: Moved %d rows to '构件表'. 整理表 now has %d data rows (parts only).",
             len(rows_to_move), ws.max_row - 1)

    ws.title = "整理表"
    return ws_comp


# ── Steps 7-9 ──────────────────────────────────────────────────

def steps_7_9_modify(wb, ws):
    """Steps 7-9: Delete 构件 columns, insert 类型/规格/宽度, BOX→BH.

    Returns (spec_new_col, width_col, part_col, spec_orig_col, len_col).
    """
    headers = get_headers(ws)

    # ---- Step 7: Verify & delete 构件-indicator columns ----
    cols_to_delete = []
    batch_col = find_col_by_keyword(headers, cfg.KW_批次)
    if batch_col is not None:
        cols_to_delete.append(batch_col)

    for kw in [cfg.KW_宽度构件, cfg.KW_高度, cfg.KW_版本, cfg.KW_备注]:
        col = find_col_by_keyword(headers, kw)
        if col is not None:
            cols_to_delete.append(col)
    # Also delete 构件名称 if present (space-delimited format)
    col_name = find_col_by_keyword(headers, "构件名称")
    if col_name is not None:
        cols_to_delete.append(col_name)

    # Second 长度 column (far-right 构件 dimension)
    len_cols = [i for i, h in enumerate(headers) if cfg.KW_长度 in safe_str(h)]
    if len(len_cols) >= 2:
        cols_to_delete.append(len_cols[-1] + 1)

    # Verify empty BEFORE deleting (skip ancillary columns like 构件名称/备注)
    _SKIP_VERIFY = {"构件名称", "备注"}
    non_empty_errors: list[str] = []
    for col in sorted(set(cols_to_delete), reverse=True):
        col_header = safe_str(ws.cell(row=1, column=col).value)
        if col_header in _SKIP_VERIFY:
            continue
        for r in range(2, ws.max_row + 1):
            if safe_str(ws.cell(row=r, column=col).value):
                non_empty_errors.append(f"'{col_header}' row {r}")
                break

    if non_empty_errors:
        raise ValueError(
            "分离零件行与构建行失败: 以下构件专属列存在非空数据 — "
            + "; ".join(non_empty_errors)
        )

    # Safe to delete
    for col in sorted(set(cols_to_delete), reverse=True):
        col_header = safe_str(ws.cell(row=1, column=col).value)
        delete_column(ws, col)
        log.info("Step 7: Deleted column '%s'.", col_header)

    # ---- Step 8: Insert columns ----
    headers = get_headers(ws)
    spec_col = find_col_by_keyword(headers, cfg.KW_规格)
    if spec_col is None:
        spec_col = find_col_by_keyword(headers, cfg.KW_型材)  # fallback: "型材" column
    part_col = find_col_by_keyword(headers, cfg.KW_零件号)

    # Rename original 规格 → 截面型材
    ws.cell(row=1, column=spec_col).value = "截面型材"
    log.info("  Renamed original col %d '规格' → '截面型材'.", spec_col)

    headers = get_headers(ws)
    part_col = find_col_by_keyword(headers, cfg.KW_零件号)
    len_col = find_col_by_keyword(headers, cfg.KW_长度)

    # 类型 left of 零件号
    insert_column(ws, part_col - 1, "类型")

    headers = get_headers(ws)
    len_col = find_col_by_keyword(headers, cfg.KW_长度)

    # 宽度 then 规格 between 截面型材 and 长度 (reverse order → correct L→R)
    insert_column(ws, len_col - 1, "宽度")
    insert_column(ws, len_col - 1, "规格")

    headers = get_headers(ws)
    sec_col = find_col_by_keyword(headers, "截面型材")
    spec_new_col = find_col_by_keyword(headers, "规格")
    width_col = find_col_by_keyword(headers, "宽度")
    len_col = find_col_by_keyword(headers, cfg.KW_长度)

    log.info("Step 8: Inserted 类型, 规格(=col %d), 宽度(=col %d).", spec_new_col, width_col)

    # ---- Step 9: Copy 截面型材 → 规格 (multi_split handles BOX natively) ----
    for r in range(2, ws.max_row + 1):
        src_val = safe_str(ws.cell(row=r, column=sec_col).value)
        if src_val:
            ws.cell(row=r, column=spec_new_col).value = src_val
    log.info("Step 9: Copied 截面型材 → 规格.")

    return spec_new_col, width_col, part_col, spec_col, len_col
