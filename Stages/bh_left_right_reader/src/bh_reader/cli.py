from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import tomllib

from . import __version__
from .analyzer import AnalyzerConfig, BHAnalyzer
from .dxf_ascii import read_ascii_dxf
from .dxf_ezdxf import read_ezdxf
from .simple_xlsx import write_results_xlsx
from .visualize import build_contact_sheets, render_three_step_sample


def _config(path: Path | None) -> AnalyzerConfig:
    if path is None:
        return AnalyzerConfig()
    with path.open("rb") as file:
        data = tomllib.load(file).get("geometry", {})
    allowed = AnalyzerConfig.__dataclass_fields__.keys()
    return AnalyzerConfig(**{key: value for key, value in data.items() if key in allowed})


def _read(path: Path, backend: str):
    if backend == "ascii":
        return read_ascii_dxf(path)
    if backend == "ezdxf":
        return read_ezdxf(path)
    try:
        return read_ezdxf(path)
    except Exception as exc:
        drawing = read_ascii_dxf(path)
        drawing.audit_messages.insert(0, f"ezdxf backend unavailable, used ASCII fallback: {exc}")
        return drawing


def _expand_inputs(values: list[str]) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            paths.extend(
                candidate
                for candidate in path.iterdir()
                if candidate.is_file() and candidate.suffix.lower() == ".dxf"
            )
        elif path.is_file() and path.suffix.lower() == ".dxf":
            paths.append(path)
        elif not path.exists():
            paths.extend(
                Path(candidate)
                for candidate in glob.glob(value)
                if Path(candidate).is_file() and Path(candidate).suffix.lower() == ".dxf"
            )
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            unique.append(resolved)
            seen.add(resolved)

    def natural_key(path: Path):
        import re
        name_parts = tuple(
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", path.name)
        )
        return name_parts, str(path).casefold()

    return sorted(unique, key=natural_key)


def _validate_io_paths(
    parser: argparse.ArgumentParser,
    *,
    inputs: list[Path],
    output: Path,
    json_path: Path,
    visual_dir: Path | None,
    config: Path | None,
) -> None:
    """Fail before reading geometry if input and generated paths are unsafe."""
    if output.suffix.lower() != ".xlsx":
        parser.error("--output must use the .xlsx extension")
    if json_path.suffix.lower() != ".json":
        parser.error("--json must use the .json extension")

    input_paths = {path.resolve() for path in inputs}
    basename_counts: dict[str, int] = {}
    for path in input_paths:
        key = path.name.casefold()
        basename_counts[key] = basename_counts.get(key, 0) + 1
    duplicate_basenames = sorted(
        name for name, count in basename_counts.items() if count > 1
    )
    if duplicate_basenames:
        parser.error(
            "input DXF basenames must be unique because result/image identifiers use the "
            f"source filename: {', '.join(duplicate_basenames)}"
        )
    input_directories = {path.parent for path in input_paths}
    output_resolved = output.resolve()
    json_resolved = json_path.resolve()
    if output_resolved == json_resolved:
        parser.error("XLSX and JSON outputs must be different paths")
    if output_resolved.exists() and json_resolved.exists() and output_resolved.samefile(json_resolved):
        parser.error("XLSX and JSON outputs resolve to the same existing file")
    for artifact_name, artifact in (
        ("XLSX output", output_resolved),
        ("JSON output", json_resolved),
    ):
        if artifact in input_paths or (
            artifact.exists() and any(artifact.samefile(path) for path in input_paths)
        ):
            parser.error(f"{artifact_name} would overwrite an input DXF: {artifact}")
        if artifact.parent in input_directories:
            parser.error(
                f"{artifact_name} must use a dedicated output subdirectory, not an input "
                f"DXF directory: {artifact}"
            )
        if artifact.exists() and artifact.is_dir():
            parser.error(f"{artifact_name} is a directory: {artifact}")

    if config is not None and not config.is_file():
        parser.error(f"configuration file does not exist or is not a file: {config}")
    if config is not None:
        config_resolved = config.resolve()
        for artifact_name, artifact in (
            ("XLSX output", output_resolved),
            ("JSON output", json_resolved),
        ):
            if artifact == config_resolved or (
                artifact.exists() and artifact.samefile(config_resolved)
            ):
                parser.error(
                    f"{artifact_name} would overwrite the configuration file: {artifact}"
                )

    if visual_dir is None:
        return
    visual_resolved = visual_dir.resolve()
    if visual_resolved.exists() and not visual_resolved.is_dir():
        parser.error(f"visual output path is not a directory: {visual_resolved}")
    if any(
        visual_resolved == directory or directory.is_relative_to(visual_resolved)
        for directory in input_directories
    ):
        parser.error(
            f"visual output directory must be separate from every input directory: {visual_resolved}"
        )
    if any(
        artifact.is_relative_to(visual_resolved)
        or visual_resolved.is_relative_to(artifact)
        for artifact in (output_resolved, json_resolved)
    ):
        parser.error("XLSX/JSON outputs and the visual directory must not contain each other")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read BH web/flange left-right setbacks from DXF")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("inputs", nargs="+", help="DXF files, glob patterns, or directories")
    parser.add_argument(
        "-o", "--output", type=Path,
        default=Path("outputs/bh_left_right_results.xlsx"),
    )
    parser.add_argument("--json", type=Path, default=None, help="optional full diagnostic JSON")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--backend", choices=("auto", "ezdxf", "ascii"), default="auto")
    parser.add_argument(
        "--visual-dir",
        type=Path,
        default=None,
        help="setback solid/dashed validation images; default: <output_stem>_visuals",
    )
    parser.add_argument("--no-visuals", action="store_true", help="do not render validation images")
    args = parser.parse_args(argv)

    paths = _expand_inputs(args.inputs)
    if not paths:
        parser.error("no DXF input found")
    json_path = args.json or args.output.with_suffix(".json")
    visual_dir = None
    if not args.no_visuals:
        visual_dir = args.visual_dir or args.output.with_name(args.output.stem + "_visuals")
    _validate_io_paths(
        parser,
        inputs=paths,
        output=args.output,
        json_path=json_path,
        visual_dir=visual_dir,
        config=args.config,
    )
    analyzer = BHAnalyzer(_config(args.config))
    results = []
    drawings = []
    for path in paths:
        try:
            drawing = _read(path, args.backend)
            drawings.append(drawing)
            results.append(analyzer.analyze(drawing))
        except Exception as exc:
            from .model import DrawingData, DrawingResult
            drawings.append(DrawingData(path, [], [], "failed", [repr(exc)]))
            results.append(DrawingResult(path.name, path.stem, "", "ERROR_UNHANDLED", 0.0, [], [repr(exc)]))

    # Render first so every tabular/JSON record contains the actual image path
    # and any rendering failure is visible in the same delivered warnings.
    visualization_map: dict[str, str] = {}
    visualization_failed = False
    if not args.no_visuals:
        assert visual_dir is not None
        individual_dir = visual_dir / "individual"
        rendered: list[Path] = []
        warning_rendered: list[Path] = []
        for drawing, result in zip(drawings, results, strict=True):
            if not drawing.primitives or not result.diagnostics.get("front_view"):
                continue
            image_path = individual_dir / f"{Path(result.file_name).stem}_左右进校验.png"
            try:
                render_three_step_sample(drawing, result, analyzer, image_path)
                rendered.append(image_path)
                visualization_map[result.file_name] = str(image_path)
                if result.status.startswith(("WARNING", "ERROR", "REVIEW")):
                    warning_rendered.append(image_path)
            except Exception as exc:
                visualization_failed = True
                result.warnings.append(f"可视化生成失败：{exc!r}")
        build_contact_sheets(rendered, visual_dir, prefix="全部图纸_左右进校验")
        build_contact_sheets(warning_rendered, visual_dir, prefix="异常图纸_左右进校验")

    rows: list[list[object]] = []
    diagnostic_rows: list[list[object]] = []
    for result in results:
        warning_text = " | ".join(result.warnings)
        if result.measurements:
            for measurement in result.measurements:
                rows.append([
                    result.file_name, result.part_number + measurement.role, result.specification,
                    measurement.left_safe, measurement.right_safe,
                    round(measurement.left_raw, 3), round(measurement.right_raw, 3),
                    result.status, round(measurement.confidence, 3),
                    measurement.evidence + (" | " + warning_text if warning_text else ""),
                    visualization_map.get(result.file_name, ""),
                ])
        else:
            rows.append([
                result.file_name, result.part_number + "（未输出）", result.specification,
                None, None, None, None, result.status,
                round(result.confidence, 3), warning_text,
                visualization_map.get(result.file_name, ""),
            ])
        front_diag = result.diagnostics.get("front_view") or {}
        unit_diag = result.diagnostics.get("units") or {}
        plate_diag = result.diagnostics.get("plate_identification") or {}
        web_diag = plate_diag.get("web") or {}
        diagnostic_rows.append([
            result.file_name,
            result.part_number,
            result.status,
            result.diagnostics.get("measurement_rule", ""),
            result.diagnostics.get("output_unit", "mm"),
            unit_diag.get("header_insunits_code"),
            unit_diag.get("header_insunits_name", ""),
            unit_diag.get("title_drawing_scale") or "",
            "否" if unit_diag else "",
            round(float(unit_diag.get("coordinate_unit_to_mm", 0.0)), 6) if unit_diag else None,
            unit_diag.get("status", ""),
            unit_diag.get("verification_mode", ""),
            front_diag.get("id", ""),
            round(float(front_diag.get("left_x_dxf", 0.0)), 6) if front_diag else None,
            round(float(front_diag.get("right_x_dxf", 0.0)), 6) if front_diag else None,
            round(float(front_diag.get("length_mm", 0.0)), 3) if front_diag else None,
            round(float(front_diag.get("height_mm", 0.0)), 3) if front_diag else None,
            plate_diag.get("upper_flange_count"),
            plate_diag.get("lower_flange_count"),
            round(float(web_diag.get("left_offset_mm", 0.0)), 3) if web_diag else None,
            round(float(web_diag.get("right_offset_mm", 0.0)), 3) if web_diag else None,
            warning_text,
            visualization_map.get(result.file_name, ""),
        ])
    write_results_xlsx(args.output, rows, diagnostic_rows)

    payload = [
        {
            "file_name": result.file_name, "part_number": result.part_number, "specification": result.specification,
            "status": result.status, "confidence": result.confidence, "warnings": result.warnings,
            "visualization_file": visualization_map.get(result.file_name, ""),
            "measurements": [measurement.__dict__ if hasattr(measurement, "__dict__") else {slot: getattr(measurement, slot) for slot in measurement.__slots__} for measurement in result.measurements],
            "diagnostics": result.diagnostics,
        }
        for result in results
    ]
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = sum(result.status == "OK" for result in results)
    print(f"processed={len(results)} ok={ok} review_or_error={len(results)-ok}")
    print(f"xlsx={args.output}")
    print(f"json={json_path}")
    if not args.no_visuals:
        print(f"visuals={visual_dir}")
    analyses_acceptable = all(
        result.status in {"OK", "REVIEW_LOW_CONFIDENCE"} for result in results
    )
    return 0 if analyses_acceptable and not visualization_failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
