#!/usr/bin/env python3
"""Excel Final command-line entry point."""

from __future__ import annotations

import logging
from pathlib import Path
import sys
from typing import Sequence

from handbook import HandbookInfrastructureError
from input_contract import InputContractError
from pipeline import run_auto_pipeline


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


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
        outcome = run_auto_pipeline(input_file, output_file)
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
