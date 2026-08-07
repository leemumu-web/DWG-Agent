from __future__ import annotations

import argparse
import glob
import json
import sys
import tomllib
from pathlib import Path

from .analyzer import AnalyzerConfig, BoxAnalyzer
from .batch import (
    BoxBatchOutcome,
    BoxInputEntry,
    BoxProgress,
    analyze_manifest,
)
from .dxf_ezdxf import read_ezdxf
from .model import DrawingResult, PlateMeasurement
from .simple_xlsx import write_results_xlsx


def _collect_files(paths: list[str]) -> list[Path]:
    """Collect DXF files from explicit paths, directories and wildcards."""
    candidates: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            candidates.extend(
                item for item in sorted(path.iterdir())
                if item.is_file() and item.suffix.lower() == ".dxf"
            )
            continue
        if any(ch in raw for ch in "*?["):
            # glob.glob handles both relative and absolute patterns
            # (Path.glob rejects non-relative patterns).
            matches = glob.glob(raw, recursive=True)
            candidates.extend(item for item in map(Path, matches) if item.is_file())
            continue
        candidates.append(path)
    # Deduplicate by resolved path.
    seen: set[Path] = set()
    result: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(path)
    return result


def _render_visuals(
    outcome: BoxBatchOutcome,
    analyzer: BoxAnalyzer,
    visual_dir: Path,
    drawings: dict[str, object],
) -> list[str]:
    from .visualize import render_box_sample

    visual_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for item in outcome.items:
        source_path = drawings.get(item.file_name)
        if source_path is None:
            continue
        try:
            drawing = read_ezdxf(source_path)
            result = next(
                (r for r in _single_results(outcome) if r.file_name == item.file_name),
                None,
            )
            if result is None:
                continue
            output = visual_dir / f"{Path(item.file_name).stem}.png"
            render_box_sample(drawing, result, analyzer, output)
            paths.append(str(output))
        except Exception as exc:
            paths.append(f"ERROR:{exc}")
    return paths


def _single_results(outcome: BoxBatchOutcome):
    # Re-run single-item analysis so render has a DrawingResult with diagnostics.
    for item in outcome.items:
        yield DrawingResult(
            file_name=item.file_name,
            part_number=item.part_number,
            specification=item.specification,
            status=item.status,
            confidence=item.confidence,
            measurements=[
                PlateMeasurement(
                    role=m.role,
                    left_raw=m.left_raw,
                    right_raw=m.right_raw,
                    left_safe=m.left_safe,
                    right_safe=m.right_safe,
                    confidence=item.confidence,
                    evidence="",
                )
                for m in item.measurements
            ],
            warnings=list(item.warnings),
            diagnostics=dict(item.diagnostics),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="box-reader", description="BOX 左右进读取器")
    parser.add_argument("paths", nargs="+", help="DXF 文件、目录或通配符")
    parser.add_argument("--config", default=None, help="TOML 配置文件路径")
    parser.add_argument("-o", "--output", default="outputs/BOX左右进结果.xlsx", help="Excel 输出路径")
    parser.add_argument("--json", default=None, help="JSON 诊断输出路径")
    parser.add_argument("--measurements", default=None, help="BOX 测量合同 JSON 输出路径（excel 阶段消费）")
    parser.add_argument("--visual-dir", default=None, help="PNG 校验图输出目录")
    parser.add_argument("--no-visuals", action="store_true", help="不生成校验图")
    parser.add_argument("-q", "--quiet", action="store_true", help="不输出进度与写入信息，仅保留最终汇总")
    args = parser.parse_args(argv)

    config = AnalyzerConfig()
    if args.config:
        with open(args.config, "rb") as handle:
            data = tomllib.load(handle)
        geometry = data.get("geometry", {})
        for key, value in geometry.items():
            if hasattr(config, key):
                setattr(config, key, value)

    paths = _collect_files(args.paths)
    if not paths:
        print("未找到任何 DXF 文件", file=sys.stderr)
        return 2

    drawings = {path.name: path for path in paths}
    analyzer = BoxAnalyzer(config)

    def on_progress(progress: BoxProgress) -> None:
        if not args.quiet:
            print(f"[{progress.processed}/{progress.total}] {progress.file_name} {progress.status}")

    outcome = analyze_manifest(
        [BoxInputEntry(path=path, file_name=path.name) for path in paths],
        on_progress=on_progress,
        analyzer=analyzer,
    )

    output = Path(args.output)
    write_results_xlsx(
        output,
        outcome.iter_result_rows(),
        outcome.iter_diagnostic_rows(),
    )
    if not args.quiet:
        print(f"Excel 已写入: {output}")

    if args.json:
        json_path = Path(args.json)
        json_path.write_text(
            json.dumps(
                [
                    {
                        "file_name": item.file_name,
                        "part_number": item.part_number,
                        "specification": item.specification,
                        "status": item.status,
                        "confidence": item.confidence,
                        "warnings": list(item.warnings),
                        "measurements": [
                            {
                                "role": m.role,
                                "left_safe": m.left_safe,
                                "right_safe": m.right_safe,
                                "left_raw": round(m.left_raw, 3),
                                "right_raw": round(m.right_raw, 3),
                            }
                            for m in item.measurements
                        ],
                        "diagnostics": item.diagnostics,
                    }
                    for item in outcome.items
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not args.quiet:
            print(f"JSON 已写入: {json_path}")

    if args.measurements:
        measurements_path = Path(args.measurements)
        measurements_path.write_text(
            json.dumps(
                {
                    "schema": "box_setback_measurements/v1",
                    "items": [
                        {
                            "file_name": item.file_name,
                            "part_number": item.part_number,
                            "specification": item.specification,
                            "status": item.status,
                            "warnings": list(item.warnings),
                            "measurements": [
                                {
                                    "role": m.role,
                                    "left_safe": m.left_safe,
                                    "right_safe": m.right_safe,
                                    "left_raw": round(m.left_raw, 3),
                                    "right_raw": round(m.right_raw, 3),
                                }
                                for m in item.measurements
                            ],
                        }
                        for item in outcome.items
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if not args.quiet:
            print(f"测量合同已写入: {measurements_path}")

    if not args.no_visuals and args.visual_dir:
        rendered = _render_visuals(outcome, analyzer, Path(args.visual_dir), drawings)
        ok = [path for path in rendered if not path.startswith("ERROR:")]
        failed = [path for path in rendered if path.startswith("ERROR:")]
        if not args.quiet:
            print(f"校验图: 成功 {len(ok)} 张，失败 {len(failed)} 张 -> {args.visual_dir}")
        for message in failed:
            print(f"  {message}", file=sys.stderr)

    print(
        f"完成: 共 {outcome.processed_count} 张，OK {outcome.ok_count} 张，"
        f"失败 {outcome.failure_count} 张"
    )
    return 0 if outcome.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
