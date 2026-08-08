"""CLI 入口。

将分类结果输出到 Excel。

用法:
    uv run python -m yikongzhe <输入目录> [--output 输出.xlsx] [--encoding utf-8]
"""

from __future__ import annotations

import argparse
import logging
import sys

from yikongzhe.classifier import classify_directory
from yikongzhe.excel_writer import write_excel

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="yikongzhe",
        description="异孔折判断 — DXF板件图形分类工具",
    )
    parser.add_argument(
        "input_dir",
        help="包含 DXF 文件的输入目录（会递归查找子目录中的 .dxf）",
    )
    parser.add_argument(
        "--output", "-o",
        default="分类结果.xlsx",
        help="输出 Excel 文件路径（默认: 分类结果.xlsx）",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="DXF 文件编码（默认: utf-8）",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="打印详细处理日志",
    )
    args = parser.parse_args()

    # 配置日志
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    logger.info("开始处理目录: %s", args.input_dir)

    try:
        results = classify_directory(args.input_dir, encoding=args.encoding)
    except Exception as e:
        logger.error("分类失败: %s", e)
        sys.exit(1)

    if not results:
        logger.warning("未在目录中找到有效的 DXF 文件")
        sys.exit(0)

    total_parts = sum(len(r.parts) for r in results)
    logger.info("处理完成: %d 个 DXF, %d 块板", len(results), total_parts)

    try:
        write_excel(results, args.output)
    except Exception as e:
        logger.error("Excel 输出失败: %s", e)
        sys.exit(1)

    logger.info("结果已保存到: %s", args.output)


if __name__ == "__main__":
    main()