"""CLI 入口。

用法:
    uv run excel-stage3 --stage2-excel <阶段2Excel> --dxf-dir <拆板后DXF目录> --output-dir <输出目录>

协议输出:
    成功时在 stdout 输出一行 JSON 结果，格式:
    DWG_EXCEL_FINAL_RESULT={"protocol_version":1,"operation":"process-stage3",...}
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from excel_stage3.stage3 import Stage3Runner

logger = logging.getLogger(__name__)

_RESULT_PREFIX = "DWG_EXCEL_FINAL_RESULT="
_PROTOCOL_VERSION = 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="excel-stage3",
        description="Excel 第三阶段处理 — 异孔折判断对接，回填 part 表图形列",
    )
    parser.add_argument(
        "--stage2-excel",
        required=True,
        help="第二阶段处理后的 Excel 文件路径",
    )
    parser.add_argument(
        "--dxf-dir",
        required=True,
        help="拆板后 DXF 文件所在目录",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="输出目录（分类结果 Excel + 深化后的 Excel）",
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

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    runner = Stage3Runner(
        stage2_excel_path=args.stage2_excel,
        dxf_dir=args.dxf_dir,
        output_dir=args.output_dir,
        encoding=args.encoding,
    )
    try:
        result = runner.run()
    except Exception:
        logger.exception("第三阶段处理失败")
        sys.exit(1)

    # Emit structured JSON result on stdout for backend parsing
    _emit_result(
        protocol_version=_PROTOCOL_VERSION,
        operation="process-stage3",
        classification_excel=result.get("classification_excel", ""),
        deepened_excel=result.get("deepened_excel", ""),
        bh_box_count=result.get("bh_box_count", 0),
        matched_count=result.get("matched_count", 0),
        unmatched_count=result.get("unmatched_count", 0),
        classified_dxf_count=result.get("classified_dxf_count", 0),
        filled_count=result.get("filled_count", 0),
        manual_count=result.get("manual_count", 0),
    )

    logger.info("第三阶段处理完成")


def _emit_result(**kwargs) -> None:
    print(
        _RESULT_PREFIX
        + json.dumps(kwargs, ensure_ascii=False, separators=(",", ":"))
    )


if __name__ == "__main__":
    main()
