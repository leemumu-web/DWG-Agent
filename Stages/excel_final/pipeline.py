"""Top-level pipeline orchestrator — runs all 25 steps in sequence.

Also provides run_init_pipeline() for initial table (初始表) format.
"""

from __future__ import annotations

import logging
from pathlib import Path

import openpyxl

from config import OUTPUT_DIR
from reader import step_0_1_load_and_clean
from transformer import steps_2_5_setup, step_6_split_sheets, steps_7_9_modify
from multi_split_bridge import step_10_multi_split
from post_split import steps_11_14_post_split
from calculator import steps_15_19_calculations
from prorate import step_prorate_split_weights
from finalize import steps_20_24_finalize
from handbook import init_handbook, close_handbook
from reader_init import read_init_table
from transform_init import transform as transform_init, build_df
from writer_parts import write_init_output, add_part_sheets
import config as cfg

log = logging.getLogger(__name__)


def run_pipeline(input_file: Path, output_file: Path | None = None):
    """Execute the full 25-step steel-part processing pipeline.

    Args:
        input_file: Path to the Tekla-exported .xls (TSV) file.
        output_file: Path for the output .xlsx.  Defaults to
                     {input_dir}/{stem}.xlsx in OUTPUT_DIR.

    Returns:
        Path to the output .xlsx file.
    """
    if output_file is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_file = OUTPUT_DIR / (input_file.stem + "_处理后.xlsx")

    log.info("=" * 60)
    log.info("钢结构零件清单处理 — 25步流水线")
    log.info("  输入: %s", input_file)
    log.info("  输出: %s", output_file)
    log.info("=" * 60)

    # ── Initialize handbook DB; infrastructure failures are fatal. ──
    init_handbook(cfg.DB_CONFIG)

    try:
        # ── Steps 0-1 ──
        wb, ws = step_0_1_load_and_clean(input_file, output_file)

        # ── Steps 2-5 ──
        comp_col, comp_qty_col, part_col, qty_col, batch_col, comp_rows = steps_2_5_setup(wb, ws)

        # ── Step 6 ──
        step_6_split_sheets(wb, ws, comp_col, comp_qty_col, part_col, qty_col, batch_col, comp_rows)
        ws = wb["整理表"]

        # ── Steps 7-9 ──
        spec_new_col, width_col, part_col, spec_orig_col, len_col = steps_7_9_modify(wb, ws)

        # Save before multi_split
        wb.save(output_file)
        wb.close()
        log.info("Saved intermediate to %s", output_file)

        # ── Step 10 ──
        wb, result_sheet = step_10_multi_split(wb, output_file)

        # ── Steps 11-14 ──
        ws = steps_11_14_post_split(wb, result_sheet)

        wb.save(output_file)
        log.info("Saved after post-split fixes to %s", output_file)

        # ── Steps 15-19 ──
        total_col, total_len_col, density_col, theo_wt_col, theo_total_wt_col = \
            steps_15_19_calculations(wb, result_sheet)

        wb.save(output_file)

        # ── Proration (between steps 19 and 20) ──
        step_prorate_split_weights(wb, result_sheet)
        wb.save(output_file)
        log.info("Saved after weight proration to %s", output_file)

        # ── Steps 20-25 ──
        steps_20_24_finalize(wb, result_sheet, total_col, theo_total_wt_col)

    finally:
        # Print handbook stats
        from handbook import get_handbook
        db = get_handbook()
        if db is not None:
            db.log_stats()
        close_handbook()

    # Generate part sheets
    add_part_sheets(wb, result_sheet)

    # Rename: 整理表 → 整理表_中间拆板前备份, 整理表_拆板后 → 整理表
    wb["整理表"].title = "整理表_中间拆板前备份"
    wb[result_sheet].title = "整理表"
    result_sheet = "整理表"

    # Final save
    wb.save(output_file)
    wb.close()
    log.info("\n" + "=" * 60)
    log.info("All 25 steps complete! Output: %s", output_file)
    log.info("Sheets: 原表, 整理表_中间拆板前备份, 构件表, 整理表, part")
    log.info("=" * 60)

    return output_file


# ── Initial table (初始表) pipeline ────────────────────────────────


def run_init_pipeline(input_file: Path, output_file: Path | None = None):
    """Process an initial table (初始表) format .xlsx into part1/part2/part3.

    This is the 9-column flat format from DWG extraction (excel-converter input).
    Uses multi_split directly via DataFrame API — no intermediate I/O.
    """
    if output_file is None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_file = OUTPUT_DIR / (input_file.stem + "_处理后.xlsx")

    log.info("=" * 60)
    log.info("初始表处理流水线")
    log.info("  输入: %s", input_file)
    log.info("  输出: %s", output_file)
    log.info("=" * 60)

    # 1. Read
    comp_info, part_rows = read_init_table(input_file)
    log.info("读取完成: 构件=%s, 零件数=%d, 构件总重=%.2f",
             comp_info.component_no, len(part_rows), comp_info.total_weight)

    # 2. Build pre-split DataFrame (整理表)
    pre_df = build_df(part_rows, comp_info)

    # 3-7. Transform via multi_split (整理表_拆板后)
    result_df = transform_init(part_rows, comp_info)
    log.info("转换完成: %d 输出行.", len(result_df))

    # 8. Write output
    write_init_output(output_file, input_file, pre_df, result_df, comp_info)

    # Rename sheets to final names
    wb = openpyxl.load_workbook(output_file)
    if "整理表" in wb.sheetnames:
        wb["整理表"].title = "整理表_中间拆板前备份"
    if "整理表_拆板后" in wb.sheetnames:
        wb["整理表_拆板后"].title = "整理表"
    wb.save(output_file)
    wb.close()

    log.info("\n" + "=" * 60)
    log.info("初始表处理完成! Output: %s", output_file)
    log.info("Sheets: 原表, 整理表_中间拆板前备份, 构件表, 整理表 (%d rows), part", len(result_df))
    log.info("=" * 60)

    return output_file
