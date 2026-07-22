#!/usr/bin/env python3
"""Excel Final command-line entry point."""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Sequence

import openpyxl

from config import INIT_TABLE_SIGNATURE
from handbook import HandbookInfrastructureError
from input_contract import InputContractError, InputKind, inspect_production_input
from pipeline import run_init_pipeline, run_pipeline


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def detect_format(filepath: Path) -> str:
    """Validate the production input first, then distinguish init from Tekla."""
    inspected = inspect_production_input(filepath)
    if inspected.kind is InputKind.TEKLA_TEXT:
        return "tsv"
    if inspected.sheet_name is None:
        raise InputContractError("validated workbook has no worksheet")

    workbook = openpyxl.load_workbook(
        inspected.path,
        read_only=True,
        data_only=True,
    )
    try:
        sheet = workbook[inspected.sheet_name]
        if sheet.title == "初始表":
            return "init"
        row2_cells = [str(sheet.cell(row=2, column=column).value or "") for column in range(1, 10)]
        matches = sum(
            1
            for keyword in INIT_TABLE_SIGNATURE
            if any(keyword in cell for cell in row2_cells)
        )
        return "init" if matches >= 7 else "tsv"
    finally:
        workbook.close()


def _parse_args(args: Sequence[str]) -> tuple[Path | None, Path | None]:
    input_file: Path | None = None
    output_file: Path | None = None
    index = 0
    while index < len(args):
        if args[index] == "-o" and index + 1 < len(args):
            output_file = Path(args[index + 1])
            index += 2
        elif not args[index].startswith("-") and input_file is None:
            input_file = Path(args[index])
            index += 1
        else:
            index += 1
    return input_file, output_file


def _print_outcome(outcome) -> None:
    print(f"\n处理完成：{outcome.output_path.name}")
    print(
        f"质量状态={outcome.quality_status}；"
        f"警告={outcome.warning_count}；严重={outcome.severe_warning_count}"
    )
    category_counts = outcome.report_summary.get("category_counts", {})
    lookup_misses = category_counts.get("五金手册查无", 0)
    if lookup_misses:
        print(
            f"提示：有 {lookup_misses} 条五金手册查无，已在整理表标红；"
            "请查看输出工作簿的处理报告。"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    input_file, output_file = _parse_args(args)
    if input_file is None:
        print(f"用法: python {Path(__file__).name} <input.xls(x)> [-o output.xlsx]", file=sys.stderr)
        return 2

    try:
        source_format = detect_format(input_file)
        logging.info("检测格式: %s", source_format)
        if source_format == "init":
            outcome = run_init_pipeline(input_file, output_file)
        else:
            outcome = run_pipeline(input_file, output_file)
    except HandbookInfrastructureError:
        print(
            "处理失败：五金手册数据库不可用，请检查服务、配置和表结构。",
            file=sys.stderr,
        )
        return 2
    except (InputContractError, FileNotFoundError, ValueError) as exc:
        print(f"处理失败：{exc}", file=sys.stderr)
        return 2

    _print_outcome(outcome)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
