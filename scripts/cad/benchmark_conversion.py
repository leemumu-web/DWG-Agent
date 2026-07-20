#!/usr/bin/env python3
"""Repeatable real-file throughput benchmark for both ODA conversion directions."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_concurrency(value: str) -> list[int]:
    """Parse a positive, unique, ordered comma-separated concurrency list."""
    try:
        values = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("concurrency 必须是逗号分隔的正整数") from exc
    if not values or any(item < 1 for item in values):
        raise ValueError("concurrency 必须至少包含一个正整数")
    return list(dict.fromkeys(values))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="真实文件 DWG↔DXF 吞吐量基准")
    parser.add_argument("--input-dir", type=Path, required=True, help="源文件目录")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 个文件；0 表示全部")
    parser.add_argument(
        "--concurrency",
        default=[1, 2, 4, 8],
        type=lambda value: parse_concurrency(value),
        help="并发列表，例如 1,2,4,8",
    )
    parser.add_argument(
        "--direction",
        required=True,
        choices=("dwg2dxf", "dxf2dwg", "roundtrip"),
    )
    parser.add_argument(
        "--mode",
        default="batch",
        choices=("batch", "file-pool", "both"),
        help="batch=并发目录分片；file-pool=每文件一次 ODA；both=两者均测",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--keep-output", action="store_true")
    return parser


def _load_converters():
    sys.path.insert(0, str(PROJECT_ROOT / "Stages/dwg2dxf/src"))
    sys.path.insert(0, str(PROJECT_ROOT / "Stages/dxf2dwg/src"))
    from dwg_converter.engines import OdaConverter as DwgToDxfConverter
    from dxf_converter.engines import OdaConverter as DxfToDwgConverter

    return DwgToDxfConverter, DxfToDwgConverter


@contextmanager
def _dedicated_display() -> Iterator[str]:
    """Own one persistent Xvfb for every ODA process in this benchmark."""
    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        raise RuntimeError("未找到 Xvfb；请安装 xorg-server-xvfb")
    display_number = next(
        (number for number in range(95, 120) if not Path(f"/tmp/.X11-unix/X{number}").exists()),
        None,
    )
    if display_number is None:
        raise RuntimeError("没有可用的 Xvfb display (:95-:119)")
    display = f":{display_number}"
    process = subprocess.Popen(
        [xvfb, display, "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    socket = Path(f"/tmp/.X11-unix/X{display_number}")
    previous = os.environ.get("DISPLAY")
    try:
        for _ in range(100):
            if socket.exists() and process.poll() is None:
                break
            if process.poll() is not None:
                raise RuntimeError(f"Xvfb {display} 启动失败")
            time.sleep(0.05)
        else:
            raise RuntimeError(f"等待 Xvfb {display} 超时")
        os.environ["DISPLAY"] = display
        yield display
    finally:
        if previous is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = previous
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _validate_output(path: Path, direction: str) -> str | None:
    if not path.is_file() or path.stat().st_size == 0:
        return "输出文件不存在或为空"
    prefix = path.read_bytes()[:4096]
    if direction == "dwg2dxf":
        text = prefix.decode("latin-1", errors="ignore").upper()
        if "SECTION" not in text and "$ACADVER" not in text:
            return "DXF 头部缺少 SECTION/$ACADVER"
    elif not prefix.startswith(b"AC10"):
        return f"DWG magic 无效: {prefix[:6]!r}"
    return None


def _stage_shards(files: list[Path], root: Path, concurrency: int) -> list[tuple[Path, Path]]:
    count = min(concurrency, len(files))
    shards = [(root / f"input-{index}", root / f"output-{index}") for index in range(count)]
    for source_dir, output_dir in shards:
        source_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)
    for index, source in enumerate(files):
        source_dir, _ = shards[index % count]
        target = source_dir / source.name
        try:
            os.link(source, target)
        except OSError:
            shutil.copy2(source, target)
    return shards


def _run_measurement(
    *,
    files: list[Path],
    direction: str,
    mode: str,
    concurrency: int,
    run_root: Path,
    converter_class,
) -> tuple[dict[str, Any], list[Path]]:
    output_ext = ".dxf" if direction == "dwg2dxf" else ".dwg"
    errors: list[str] = []
    outputs: list[Path] = []
    results: list[Any] = []

    start = time.monotonic()
    if mode == "batch":
        shards = _stage_shards(files, run_root, concurrency)

        def convert_shard(paths: tuple[Path, Path]):
            source_dir, output_dir = paths
            converter = converter_class(xvfb_run=False)
            return converter.convert_directory(source_dir, output_dir)

        with ThreadPoolExecutor(max_workers=len(shards)) as pool:
            batches = list(pool.map(convert_shard, shards))
        results = [result for batch in batches for result in batch.results]
    else:
        output_dir = run_root / "output"
        output_dir.mkdir(parents=True)

        def convert_file(source: Path):
            converter = converter_class(xvfb_run=False)
            return converter.convert_file(source, output_dir)

        with ThreadPoolExecutor(max_workers=min(concurrency, len(files))) as pool:
            results = list(pool.map(convert_file, files))
    elapsed = time.monotonic() - start

    for result in results:
        source_name = Path(result.source).name
        if not result.success:
            errors.append(f"{source_name}: {result.error or 'ODA 转换失败'}")
            continue
        validation_error = _validate_output(Path(result.target), direction)
        if validation_error:
            errors.append(f"{source_name}: {validation_error}")
            continue
        outputs.append(Path(result.target))

    if len(results) < len(files):
        errors.extend(["未返回结果"] * (len(files) - len(results)))
    succeeded = len(outputs)
    metric = {
        "direction": direction,
        "mode": mode,
        "concurrency": concurrency,
        "total": len(files),
        "succeeded": succeeded,
        "failed": len(files) - succeeded,
        "elapsed_seconds": round(elapsed, 3),
        "files_per_second": round(succeeded / elapsed, 3) if elapsed else 0.0,
        "input_bytes": sum(path.stat().st_size for path in files),
        "error_filenames": errors,
        "output_extension": output_ext,
    }
    return metric, outputs


def _print_metric(metric: dict[str, Any]) -> None:
    print(
        f"{metric['direction']:8s} {metric['mode']:9s} c={metric['concurrency']:<2d} "
        f"{metric['succeeded']}/{metric['total']} 成功, "
        f"{metric['elapsed_seconds']:.3f}s, {metric['files_per_second']:.3f} files/s"
    )
    for error in metric["error_filenames"]:
        print(f"  失败: {error}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except ValueError as exc:
        parser.error(str(exc))
    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        parser.error(f"输入目录不存在: {input_dir}")
    if args.limit < 0:
        parser.error("--limit 不能为负数")

    input_ext = ".dxf" if args.direction == "dxf2dwg" else ".dwg"
    source_files = sorted(
        path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == input_ext
    )
    if args.limit:
        source_files = source_files[: args.limit]
    if not source_files:
        parser.error(f"输入目录中没有 {input_ext} 文件: {input_dir}")

    modes = ("batch", "file-pool") if args.mode == "both" else (args.mode,)
    DwgToDxfConverter, DxfToDwgConverter = _load_converters()
    metrics: list[dict[str, Any]] = []
    temp_manager = None
    if args.keep_output:
        output_root = Path(tempfile.mkdtemp(prefix="cad-benchmark-"))
        print(f"保留输出目录: {output_root}")
    else:
        temp_manager = tempfile.TemporaryDirectory(prefix="cad-benchmark-")
        output_root = Path(temp_manager.name)

    try:
        with _dedicated_display() as display:
            print(f"使用持久 Xvfb DISPLAY={display}，文件数={len(source_files)}")
            for mode in modes:
                for concurrency in args.concurrency:
                    combo_root = output_root / f"{mode}-c{concurrency}"
                    if args.direction in {"dwg2dxf", "roundtrip"}:
                        forward, dxf_outputs = _run_measurement(
                            files=source_files,
                            direction="dwg2dxf",
                            mode=mode,
                            concurrency=concurrency,
                            run_root=combo_root / "dwg2dxf",
                            converter_class=DwgToDxfConverter,
                        )
                        metrics.append(forward)
                        _print_metric(forward)
                        if args.direction == "roundtrip" and dxf_outputs:
                            reverse, _ = _run_measurement(
                                files=dxf_outputs,
                                direction="dxf2dwg",
                                mode=mode,
                                concurrency=concurrency,
                                run_root=combo_root / "dxf2dwg",
                                converter_class=DxfToDwgConverter,
                            )
                            metrics.append(reverse)
                            _print_metric(reverse)
                    else:
                        reverse, _ = _run_measurement(
                            files=source_files,
                            direction="dxf2dwg",
                            mode=mode,
                            concurrency=concurrency,
                            run_root=combo_root / "dxf2dwg",
                            converter_class=DxfToDwgConverter,
                        )
                        metrics.append(reverse)
                        _print_metric(reverse)
    finally:
        if temp_manager is not None:
            temp_manager.cleanup()

    summary = {
        "input_dir": str(input_dir),
        "source_files": len(source_files),
        "results": metrics,
    }
    if args.json_output:
        json_output = args.json_output.expanduser().resolve()
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(f"JSON: {json_output}")
    return 1 if any(metric["failed"] for metric in metrics) else 0


if __name__ == "__main__":
    raise SystemExit(main())
