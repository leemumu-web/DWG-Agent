from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .compiler import XBOX_REPORT_SCHEMA, batch_payload, compile_xbox_batch
from .contracts import XboxSplitError
from .release import load_verified_xbox_release_attestation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steel-dxf-split-xbox",
        description="独立的 Tekla XBOX 封闭箱形构件 DXF 拆板工具（成对产物：正常版 + 焊接余量版）。",
    )
    parser.add_argument("input", type=Path, help="包含待处理 XBOX 源 DXF 的输入目录")
    parser.add_argument("--output-dir", required=True, type=Path, help="XBOX 拆板结果输出目录")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="仅覆盖本工具拥有的同名任务结果和报告",
    )
    parser.add_argument(
        "--authorize-project-tekla-xbox-dxf-v1",
        action="store_true",
        help="声明输入来自平台冻结并分类确认的 Tekla XBOX DXF",
    )
    parser.add_argument(
        "--xbox-release-attestation",
        type=Path,
        default=None,
        help="可选：显式指定 XBOX release attestation 文件路径。",
    )
    return parser


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        try:
            load_verified_xbox_release_attestation(args.xbox_release_attestation)
        except ValueError as error:
            raise XboxSplitError(
                "XBOX_RELEASE_ATTESTATION_UNAVAILABLE",
                f"XBOX 发布认证不可用：{error}",
            ) from error
        batch = compile_xbox_batch(
            args.input,
            args.output_dir,
            overwrite=args.overwrite,
            release_attestation_path=args.xbox_release_attestation,
        )
    except XboxSplitError as error:
        _print(
            {
                "status": "fatal",
                "error": {"code": error.code, "message_zh": error.message_zh},
            }
        )
        return 2
    payload = batch_payload(batch)
    payload["schema"] = XBOX_REPORT_SCHEMA
    _print(payload)
    return batch.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
