from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .compiler import batch_payload, split_pl
from .contracts import PLSplitError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steel-dxf-split-pl",
        description="独立的 Tekla PL 折弯板 DXF 拆板工具。",
    )
    parser.add_argument("input", type=Path, help="单张 DXF、合并 DXF 或输入目录")
    parser.add_argument("--output-dir", required=True, type=Path, help="PL 单件结果目录")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="仅覆盖本工具拥有的同名零件结果和报告",
    )
    return parser


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        batch = split_pl(
            args.input,
            args.output_dir,
            overwrite=args.overwrite,
        )
    except PLSplitError as error:
        _print(
            {
                "status": "fatal",
                "error": {"code": error.code, "message_zh": error.message_zh},
            }
        )
        return 2
    except Exception as error:
        _print(
            {
                "status": "fatal",
                "error": {
                    "code": "UNEXPECTED_ERROR",
                    "message_zh": str(error),
                },
            }
        )
        return 2
    _print(batch_payload(batch))
    return batch.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
