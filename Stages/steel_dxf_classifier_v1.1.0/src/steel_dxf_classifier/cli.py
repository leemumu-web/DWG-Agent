from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import sys

from . import __version__
from .batch import classify_directory


CLI_SCHEMA = "STEEL-DXF-CLI-1.1"
USAGE_ERROR_EXIT = 64


class _UsageError(ValueError):
    pass


class _ContractArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _ContractArgumentParser(
        prog="steel-dxf-classify",
        description="按零件图右上标题栏截面字段分类第一层 DXF 文件。",
    )
    parser.add_argument(
        "input_directory",
        nargs="?",
        help="名称为 <项目名称>_dxf 的输入目录",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="事务式替换该项目已有分类目录和报告",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="向 stdout 输出单个 JSON 摘要对象",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="输出版本并退出",
    )
    return parser


def _exit_code(summary) -> int:
    return 2 if summary.review_required_count or summary.unreadable_count else 0


def _json_payload(summary, exit_code: int) -> dict[str, object]:
    return {
        "schema": CLI_SCHEMA,
        "status": "completed_with_review" if exit_code == 2 else "completed",
        "exit_code": exit_code,
        "summary": summary.to_dict(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.version:
            print(f"steel-dxf-classifier {__version__}")
            return 0
        if args.input_directory is None:
            raise _UsageError("缺少输入目录 <项目名称>_dxf")
    except _UsageError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return USAGE_ERROR_EXIT

    try:
        summary = classify_directory(
            args.input_directory,
            overwrite=args.overwrite,
        )
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return USAGE_ERROR_EXIT
    except (FileExistsError, OSError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1

    exit_code = _exit_code(summary)
    if args.json:
        print(json.dumps(_json_payload(summary, exit_code), ensure_ascii=False, sort_keys=True))
        return exit_code

    print(f"项目: {summary.project_name}")
    print(f"输入: {summary.input_count}")
    print(f"已分类: {summary.classified_count}")
    print(f"待确认: {summary.review_required_count}")
    print(f"无法读取: {summary.unreadable_count}")
    for part_type, count in sorted(summary.type_counts.items()):
        print(f"  {part_type}: {count}")
    print(f"耗时: {summary.elapsed_seconds:.3f} 秒")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
