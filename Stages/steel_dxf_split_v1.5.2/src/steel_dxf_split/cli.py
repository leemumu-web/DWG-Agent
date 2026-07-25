from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

from . import __version__
from .bh_knowledge import BHSourceContract
from .bh_project_ledger import publish_bh_project_ledger
from .box.contracts import BOX_EXPORT_PROFILE, BoxSourceContract
from .pipeline import SplitOptions, SplitResult, split_dxf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "扫描一个输入目录，自动识别 BH/BOX；每张自动验收图成对生成普通版与余量伸长版 DXF。"
        )
    )
    parser.add_argument("input_dir", type=Path, help="仅包含待处理源 DXF 的输入目录。")
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument(
        "--authorize-tekla-bh-single-part-profile",
        metavar="PROFILE_ID",
        help="授权 BH 输入使用指定的 Tekla 单构件导出配置。",
    )
    parser.add_argument(
        "--authorize-tekla-box-single-part-profile",
        choices=(BOX_EXPORT_PROFILE,),
        default=None,
        help="授权 BOX 输入使用受支持的 Tekla 单构件导出配置。",
    )
    parser.add_argument(
        "--box-release-attestation",
        type=Path,
        default=None,
        help="可选：显式指定 BOX release attestation。",
    )
    return parser


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _snapshot_inputs(input_dir: Path, output_dir: Path) -> tuple[Path, ...]:
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise ValueError("输入路径必须是普通目录，且不能是符号链接。")
    if _is_inside(output_dir, input_dir) or _is_inside(input_dir, output_dir):
        raise ValueError("输入目录与输出目录不得相同或互相嵌套。")
    inputs = tuple(
        sorted(
            (
                path
                for path in input_dir.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.casefold() == ".dxf"
            ),
            key=lambda path: path.name.casefold(),
        )
    )
    if not inputs:
        raise ValueError("输入目录中没有可处理的 DXF 文件。")
    task_names = [
        path.stem.replace("_拆板前", "").replace("拆板前", "").rstrip("_- ")
        for path in inputs
    ]
    if any(not name for name in task_names):
        raise ValueError("输入 DXF 的任务名称不能为空。")
    if len({name.casefold() for name in task_names}) != len(task_names):
        raise ValueError("输入目录中存在会写入同一任务目录的重名 DXF。")
    return inputs


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inputs = _snapshot_inputs(args.input_dir, args.output_dir)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    options = SplitOptions(
        source_contract=(
            BHSourceContract(
                export_profile=args.authorize_tekla_bh_single_part_profile
            )
            if args.authorize_tekla_bh_single_part_profile
            else None
        ),
        box_source_contract=(
            BoxSourceContract(
                export_profile=args.authorize_tekla_box_single_part_profile
            )
            if args.authorize_tekla_box_single_part_profile
            else None
        ),
        box_release_attestation=args.box_release_attestation,
    )
    summaries: list[dict[str, object]] = []
    results: list[SplitResult] = []
    failures = 0
    for input_path in inputs:
        processing_started = perf_counter()
        try:
            result = split_dxf(input_path, args.output_dir, options)
        except Exception as exc:
            failures += 1
            summaries.append(
                {
                    "input": str(input_path),
                    "compiler_version": __version__,
                    "automation_route": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            continue
        if isinstance(result, SplitResult):
            results.append(result)
        summaries.append(
            result.to_summary(
                input_path=input_path,
                compiler_version=__version__,
                processing_seconds=perf_counter() - processing_started,
            )
        )
    if not failures:
        try:
            publish_bh_project_ledger(results, args.output_dir)
        except Exception as exc:
            failures += 1
            summaries.append(
                {
                    "input": str(args.input_dir),
                    "compiler_version": __version__,
                    "automation_route": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    if failures:
        return 2
    if any(item.get("automation_route") != "auto_accepted" for item in summaries):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
