"""命令行入口。

用法:
    python -m dwg_converter --check-env
    python -m dwg_converter path/to/a.dwg [-o out]
    python -m dwg_converter path/to/dir  [-o out] [-r]

默认输出目录为 samples/output（相对项目根）。
退出码：0 全部成功；1 有转换失败；2 环境错误（ODA/xvfb 缺失）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .check_env import print_environment_report
from .engines.oda_converter import BatchResult, OdaConvertError
from .engines.oda_converter import ConvertResult
from .service import convert


# 默认输出目录：项目根/samples/output
_DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2] / "samples" / "output"
)


def _print_single(result: ConvertResult) -> int:
    mark = "OK" if result.success else "FAIL"
    print(f"  [{mark}] {result.source} -> {result.target} ({result.duration:.2f}s)")
    if result.error:
        print(f"        error: {result.error}")
    return 0 if result.success else 1


def _print_batch(batch: BatchResult) -> int:
    print(f"  批量转换: {batch.ok}/{batch.total} 成功, "
          f"{batch.failed} 失败, 耗时 {batch.duration:.2f}s")
    for r in batch.results:
        mark = "OK" if r.success else "FAIL"
        line = f"  [{mark}] {r.source.name} -> {r.target.name} ({r.duration:.2f}s)"
        if r.error:
            line += f"  err: {r.error[:80]}"
        print(line)
    return 0 if batch.all_success else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dwg_converter",
        description="DWG → DXF 转换（基于 ODA File Converter）",
    )
    parser.add_argument(
        "source", nargs="?",
        help="单个 .dwg 文件或包含 .dwg 的目录（--check-env 时可省略）",
    )
    parser.add_argument(
        "-o", "--output", default=str(_DEFAULT_OUTPUT),
        help=f"输出目录（默认 {_DEFAULT_OUTPUT}）",
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="递归批量转换")
    parser.add_argument("--version", default="ACAD2018", help="DXF 版本 (默认 ACAD2018)")
    parser.add_argument("--no-audit", action="store_true", help="关闭 audit")
    parser.add_argument("--timeout", type=int, default=None, help="单次转换超时秒（默认 engine 决定）")
    parser.add_argument("--check-env", action="store_true", help="仅做环境检查后退出")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.check_env:
        status = print_environment_report()
        return 0 if status.ok else 2

    if args.source is None:
        parser.error("缺少 source 参数（或使用 --check-env 仅做环境检查）")

    source = Path(args.source)
    out = Path(args.output)
    audit = not args.no_audit

    try:
        result = convert(
            source=source,
            target_dir=out,
            version=args.version,
            audit=audit,
            recursive=args.recursive,
            timeout=args.timeout,
        )
    except OdaConvertError as e:
        # 环境错误（ODA/xvfb 缺失）→ 退出码 2，区别于转换失败（1）。
        print(f"环境错误: {e}", file=sys.stderr)
        return 2

    if isinstance(result, ConvertResult):
        return _print_single(result)
    return _print_batch(result)


if __name__ == "__main__":
    sys.exit(main())
