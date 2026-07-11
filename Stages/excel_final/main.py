#!/usr/bin/env python3
"""钢结构零件清单处理 — CLI 入口.

自适应处理 Tekla TSV 格式和 初始表 格式。

Usage:
    python main.py [input.xls(x)] [-o output.xlsx]
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path

import openpyxl

# Ensure the project root is on sys.path so `import config` etc. work
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from pipeline import run_pipeline, run_init_pipeline
from config import DEFAULT_INPUT, INIT_TABLE_SIGNATURE

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def detect_format(filepath: Path) -> str:
    """Auto-detect input format: 'init' for 初始表, 'tsv' for Tekla TSV."""
    if filepath.suffix.lower() in (".xlsx", ".xlsm"):
        try:
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            # Check for 初始表 sheet
            if "初始表" in wb.sheetnames:
                wb.close()
                return "init"
            # Check first sheet's Row 2 for 初始表 signature
            ws = wb.worksheets[0]
            row2_cells = [str(ws.cell(row=2, column=c).value or "") for c in range(1, 10)]
            match_count = sum(
                1 for kw in INIT_TABLE_SIGNATURE
                if any(kw in cell for cell in row2_cells)
            )
            wb.close()
            if match_count >= 7:
                return "init"
        except Exception:
            pass
    return "tsv"


def main():
    args = sys.argv[1:]

    input_file = None
    output_file = None
    i = 0
    while i < len(args):
        if args[i] == "-o" and i + 1 < len(args):
            output_file = Path(args[i + 1])
            i += 2
        elif not args[i].startswith("-") and input_file is None:
            input_file = Path(args[i])
            i += 1
        else:
            i += 1

    if input_file is None:
        if DEFAULT_INPUT.exists():
            input_file = DEFAULT_INPUT
        else:
            print(f"Usage: python {__file__} <input.xls(x)> [-o output.xlsx]")
            print(f"Default input not found: {DEFAULT_INPUT}")
            sys.exit(1)

    # Auto-detect format and route to appropriate pipeline
    fmt = detect_format(input_file)
    logging.info("Detected format: %s", fmt)

    if fmt == "init":
        output_path = run_init_pipeline(input_file, output_file)
    else:
        output_path = run_pipeline(input_file, output_file)

    print(f"\nDone. Output: {output_path}")


if __name__ == "__main__":
    main()
