from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from time import perf_counter

from . import __version__
from .bh_knowledge import BHSourceContract
from .bh_project_ledger import publish_bh_project_ledger
from .box.contracts import BOX_EXPORT_PROFILE, BoxSourceContract
from .box.release import load_verified_box_release_attestation
from .pipeline import SplitOptions, SplitResult, split_classified_dxf

QUANTITY_CHECK_INTERVAL = 30

CLASSIFIED_INPUT_SCHEMA = "STEEL-DXF-CLASSIFIED-SPLIT-INPUT-1.0"


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是正整数") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "按照上游冻结分类将 BH/BOX 直接交给对应拆板核心，并成对生成普通版与余量伸长版 DXF。"
        )
    )
    parser.add_argument("input_dir", type=Path, help="仅包含待处理源 DXF 的输入目录。")
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument(
        "--classification-manifest",
        type=Path,
        required=True,
        help="上游冻结的文件名与 BH/BOX 类型一一对应清单。",
    )
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
    parser.add_argument(
        "--progress-json",
        type=Path,
        default=None,
        help="可选：逐图完成后原子更新平台进度 JSON。",
    )
    parser.add_argument(
        "--lean-report",
        action="store_true",
        default=False,
        help=(
            "精简报告：report.json 只保留验收所需字段，不生成 PNG 预览；"
            "weld_allowance_report.json 照常生成。BH 与 BOX 均生效。"
        ),
    )
    parser.add_argument(
        "--workers",
        type=_positive_int,
        default=1,
        metavar="N",
        help="独立图纸处理进程数；默认 1，必须是正整数。",
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


def _load_classified_inputs(
    path: Path,
    inputs: tuple[Path, ...],
) -> dict[Path, str]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"分类清单不可读取: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"分类清单不是有效 JSON: {path}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "items"}:
        raise ValueError("分类清单顶层字段无效")
    if payload.get("schema") != CLASSIFIED_INPUT_SCHEMA:
        raise ValueError("分类清单 schema 无效")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("分类清单 items 必须是数组")

    classified_by_name: dict[str, str] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != {"file_name", "family"}:
            raise ValueError(f"分类清单第 {index + 1} 项字段无效")
        file_name = item.get("file_name")
        family = item.get("family")
        if not isinstance(file_name, str) or not file_name:
            raise ValueError(f"分类清单第 {index + 1} 项 file_name 无效")
        if (
            Path(file_name).name != file_name
            or Path(file_name).suffix.casefold() != ".dxf"
        ):
            raise ValueError(f"分类清单文件名不安全或不是 DXF: {file_name}")
        if family not in {"BH", "BOX"}:
            raise ValueError(f"分类清单类型不支持: {family}")
        name_key = file_name.casefold()
        if name_key in classified_by_name:
            raise ValueError(f"分类清单文件名重复: {file_name}")
        classified_by_name[name_key] = family

    input_by_name = {input_path.name.casefold(): input_path for input_path in inputs}
    extra_names = sorted(set(classified_by_name) - set(input_by_name))
    if extra_names:
        raise ValueError(f"分类清单包含额外文件: {extra_names[0]}")
    missing_names = sorted(set(input_by_name) - set(classified_by_name))
    if missing_names:
        raise ValueError(f"分类清单缺少输入文件: {missing_names[0]}")
    return {
        input_path: classified_by_name[input_path.name.casefold()]
        for input_path in inputs
    }


def _publish_progress(
    path: Path | None,
    *,
    processed_count: int,
    input_count: int,
    auto_accepted_count: int,
    manual_review_count: int,
    failed_count: int,
) -> None:
    if path is None:
        return
    if path.is_symlink():
        raise ValueError("进度文件不能是符号链接。")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema": "STEEL-DXF-SPLIT-PROGRESS-1",
                "processed_count": processed_count,
                "input_count": input_count,
                "auto_accepted_count": auto_accepted_count,
                "manual_review_count": manual_review_count,
                "failed_count": failed_count,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _verify_quantity_checkpoint(
    *,
    processed_count: int,
    result_count: int,
    auto_accepted_count: int,
    manual_review_count: int,
    failed_count: int,
) -> None:
    """Prove that only drawings contribute to each 30-file checkpoint."""
    checkpoint_start = (
        ((processed_count - 1) // QUANTITY_CHECK_INTERVAL)
        * QUANTITY_CHECK_INTERVAL
        + 1
    )
    if result_count != processed_count:
        raise ValueError(
            f"图纸数量核验失败（{checkpoint_start}-{processed_count}）："
            f"已处理 {processed_count}，图纸结果 {result_count}。"
        )
    accounted = auto_accepted_count + manual_review_count + failed_count
    if accounted != processed_count:
        raise ValueError(
            f"图纸数量核验失败（{checkpoint_start}-{processed_count}）："
            f"已处理 {processed_count}，业务分类合计 {accounted}。"
        )


def _run_split_task(
    input_path: Path,
    output_dir: Path,
    options: SplitOptions,
    family: str,
) -> dict[str, object]:
    """Run one isolated drawing; only the parent publishes batch state."""

    started = perf_counter()
    try:
        result = split_classified_dxf(
            input_path,
            output_dir,
            options,
            family=family,
        )
    except Exception as exc:
        return {
            "input_path": input_path,
            "result": None,
            "processing_seconds": perf_counter() - started,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return {
        "input_path": input_path,
        "result": result,
        "processing_seconds": perf_counter() - started,
        "error_type": None,
        "error": None,
    }


def _failed_split_task(
    input_path: Path,
    exc: Exception,
) -> dict[str, object]:
    return {
        "input_path": input_path,
        "result": None,
        "processing_seconds": 0.0,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _iter_split_tasks(
    inputs: tuple[Path, ...],
    output_dir: Path,
    options: SplitOptions,
    classified_inputs: dict[Path, str],
    *,
    workers: int,
):
    if workers == 1:
        for input_path in inputs:
            yield _run_split_task(
                input_path,
                output_dir,
                options,
                classified_inputs[input_path],
            )
        return
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _run_split_task,
                input_path,
                output_dir,
                options,
                classified_inputs[input_path],
            ): input_path
            for input_path in inputs
        }
        for future in as_completed(futures):
            input_path = futures[future]
            try:
                yield future.result()
            except Exception as exc:
                yield _failed_split_task(input_path, exc)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inputs = _snapshot_inputs(args.input_dir, args.output_dir)
        classified_inputs = _load_classified_inputs(
            args.classification_manifest,
            inputs,
        )
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
        lean_report=args.lean_report,
    )
    if "BOX" in classified_inputs.values():
        try:
            load_verified_box_release_attestation(
                args.box_release_attestation,
            )
        except (OSError, ValueError) as exc:
            print(f"错误：BOX 生产认证不可用：{exc}", file=sys.stderr)
            return 3
    summaries: list[dict[str, object]] = []
    summary_entries: list[tuple[Path, dict[str, object]]] = []
    result_entries: list[tuple[Path, SplitResult]] = []
    failures = 0
    auto_accepted_count = 0
    manual_review_count = 0
    for processed_count, task in enumerate(
        _iter_split_tasks(
            inputs,
            args.output_dir,
            options,
            classified_inputs,
            workers=args.workers,
        ),
        start=1,
    ):
        input_path = task["input_path"]
        if not isinstance(input_path, Path):
            input_path = Path(str(input_path))
        error_type = task.get("error_type")
        error = task.get("error")
        result = task.get("result")
        if error_type is not None or error is not None or result is None:
            failures += 1
            summary_entries.append(
                (
                    input_path,
                    {
                        "input": str(input_path),
                        "compiler_version": __version__,
                        "automation_route": "failed",
                        "error_type": str(error_type or "WorkerError"),
                        "error": str(error or "split task returned no result"),
                    },
                )
            )
            if (
                processed_count % QUANTITY_CHECK_INTERVAL == 0
                or processed_count == len(inputs)
            ):
                _verify_quantity_checkpoint(
                    processed_count=processed_count,
                    result_count=len(summary_entries),
                    auto_accepted_count=auto_accepted_count,
                    manual_review_count=manual_review_count,
                    failed_count=failures,
                )
            _publish_progress(
                args.progress_json,
                processed_count=processed_count,
                input_count=len(inputs),
                auto_accepted_count=auto_accepted_count,
                manual_review_count=manual_review_count,
                failed_count=failures,
            )
            continue
        if isinstance(result, SplitResult):
            result_entries.append((input_path, result))
        summary = result.to_summary(
            input_path=input_path,
            compiler_version=__version__,
            processing_seconds=float(task.get("processing_seconds", 0.0)),
        )
        summary_entries.append((input_path, summary))
        if summary.get("automation_route") == "auto_accepted":
            auto_accepted_count += 1
        else:
            manual_review_count += 1
        if (
            processed_count % QUANTITY_CHECK_INTERVAL == 0
            or processed_count == len(inputs)
        ):
            _verify_quantity_checkpoint(
                processed_count=processed_count,
                result_count=len(summary_entries),
                auto_accepted_count=auto_accepted_count,
                manual_review_count=manual_review_count,
                failed_count=failures,
            )
        _publish_progress(
            args.progress_json,
            processed_count=processed_count,
            input_count=len(inputs),
            auto_accepted_count=auto_accepted_count,
            manual_review_count=manual_review_count,
            failed_count=failures,
        )
    input_order = {input_path: index for index, input_path in enumerate(inputs)}
    summaries = [
        summary
        for _input_path, summary in sorted(
            summary_entries,
            key=lambda item: input_order[item[0]],
        )
    ]
    results = [
        result
        for _input_path, result in sorted(
            result_entries,
            key=lambda item: input_order[item[0]],
        )
    ]
    try:
        publish_bh_project_ledger(results, args.output_dir)
    except Exception as exc:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
        print(f"错误：{exc}", file=sys.stderr)
        return 3
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    if failures:
        return 2
    if any(item.get("automation_route") != "auto_accepted" for item in summaries):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
