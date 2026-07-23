#!/usr/bin/env python3
"""Calibrate the remnant parser against an external DWG/DXF corpus.

The command writes metadata and parser candidates only. Source drawings remain
in the explicitly supplied input directory and are never copied to the report
directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
STAGE_SRC = REPO_ROOT / "Stages" / "remnant_drawing_reader" / "src"
for import_root in (BACKEND_ROOT, STAGE_SRC):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


@dataclass(frozen=True, slots=True)
class DrawingInput:
    index: int
    path: Path
    relative_path: str
    format: str
    sha256: str
    size_bytes: int
    dwg_version: str | None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_drawings(input_dir: Path) -> list[DrawingInput]:
    """Recursively enumerate supported drawings in deterministic path order."""
    root = input_dir.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"input directory does not exist: {input_dir}")
    paths = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in {".dwg", ".dxf"}),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )
    drawings: list[DrawingInput] = []
    for index, path in enumerate(paths, start=1):
        file_format = path.suffix.lower()[1:]
        if file_format == "dwg":
            with path.open("rb") as stream:
                header = stream.read(6)
        else:
            header = b""
        drawings.append(
            DrawingInput(
                index=index,
                path=path,
                relative_path=path.relative_to(root).as_posix(),
                format=file_format,
                sha256=_sha256(path),
                size_bytes=path.stat().st_size,
                dwg_version=header.decode("ascii", errors="replace") if file_format == "dwg" else None,
            )
        )
    return drawings


def _candidate_values(items: Sequence[Any]) -> list[str]:
    return [str(item.value) for item in items]


def _parse_payload(path: Path) -> dict[str, Any]:
    from remnant_drawing_reader import parse_dxf

    result = parse_dxf(path)
    return result.to_dict()


def _public_row(drawing: DrawingInput) -> dict[str, Any]:
    row = asdict(drawing)
    row.pop("path")
    row.update(
        {
            "conversion_status": "not_run",
            "parse_status": "not_run",
            "material_candidates": [],
            "project_candidates": [],
            "part_candidates": [],
            "warnings": [],
        }
    )
    return row


def _default_converter(inputs: Mapping[int, Path], output_dir: Path) -> Mapping[int, Path]:
    from app.modules.cad_processing.interface import convert_dwg_directory

    return convert_dwg_directory(inputs, output_dir)


def build_corpus_report(
    drawings: Sequence[DrawingInput],
    *,
    convert: bool = True,
    converter: Callable[[Mapping[int, Path], Path], Mapping[int, Path]] | None = None,
) -> dict[str, Any]:
    """Convert once per corpus, parse independently, and return JSON-safe metadata."""
    rows = {drawing.index: _public_row(drawing) for drawing in drawings}
    if convert:
        with tempfile.TemporaryDirectory(prefix="remnant-corpus-") as temporary:
            workspace = Path(temporary)
            staging = workspace / "input"
            converted_dir = workspace / "converted"
            staging.mkdir()
            conversion_inputs: dict[int, Path] = {}
            parse_inputs: dict[int, Path] = {}
            for drawing in drawings:
                if drawing.format == "dwg":
                    staged = staging / f"{drawing.index:06}.dwg"
                    shutil.copyfile(drawing.path, staged)
                    conversion_inputs[drawing.index] = staged
                else:
                    parse_inputs[drawing.index] = drawing.path
                    rows[drawing.index]["conversion_status"] = "not_required"
            if conversion_inputs:
                try:
                    outputs = (converter or _default_converter)(conversion_inputs, converted_dir)
                except Exception as exc:  # operational report must retain per-corpus evidence
                    outputs = {}
                    error_code = type(exc).__name__
                    for item_id in conversion_inputs:
                        rows[item_id]["conversion_status"] = "failed"
                        rows[item_id]["warnings"].append({"code": "CONVERSION_FAILED", "message": error_code})
                for item_id, output in outputs.items():
                    parse_inputs[item_id] = Path(output)
                    rows[item_id]["conversion_status"] = "converted"
                for item_id in conversion_inputs.keys() - outputs.keys():
                    if rows[item_id]["conversion_status"] == "not_run":
                        rows[item_id]["conversion_status"] = "failed"
            for item_id, dxf_path in parse_inputs.items():
                try:
                    parsed = _parse_payload(dxf_path)
                except Exception as exc:
                    rows[item_id]["parse_status"] = "failed"
                    rows[item_id]["warnings"].append(
                        {"code": "PARSE_FAILED", "message": type(exc).__name__}
                    )
                    continue
                rows[item_id]["parse_status"] = "parsed"
                rows[item_id]["material_candidates"] = parsed["material_candidates"]
                rows[item_id]["project_candidates"] = parsed["project_candidates"]
                rows[item_id]["part_candidates"] = parsed["part_candidates"]
                rows[item_id]["warnings"].extend(parsed["warnings"])

    ordered = [rows[index] for index in sorted(rows)]
    return {
        "schema_version": "1.0",
        "aggregate": {
            "drawing_count": len(ordered),
            "dwg_count": sum(row["format"] == "dwg" for row in ordered),
            "dxf_count": sum(row["format"] == "dxf" for row in ordered),
            "converted_count": sum(row["conversion_status"] == "converted" for row in ordered),
            "parsed_count": sum(row["parse_status"] == "parsed" for row in ordered),
            "conversion_failed_count": sum(row["conversion_status"] == "failed" for row in ordered),
            "parse_failed_count": sum(row["parse_status"] == "failed" for row in ordered),
        },
        "drawings": ordered,
    }


def _flat_values(candidates: Sequence[dict[str, Any]]) -> str:
    return " | ".join(str(item["value"]) for item in candidates)


def write_corpus_report(input_dir: Path, output_dir: Path, *, convert: bool = True) -> dict[str, Any]:
    source = input_dir.expanduser().resolve()
    destination = output_dir.expanduser().resolve()
    if source == destination:
        raise ValueError("output directory must differ from input directory")
    drawings = discover_drawings(source)
    report = build_corpus_report(drawings, convert=convert)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "relative_path", "format", "sha256", "size_bytes", "dwg_version",
        "conversion_status", "parse_status", "material_candidates", "project_candidates",
        "part_candidates", "warning_codes",
    ]
    with (destination / "candidates.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in report["drawings"]:
            writer.writerow(
                {
                    **{field: row[field] for field in fields[:7]},
                    "material_candidates": _flat_values(row["material_candidates"]),
                    "project_candidates": _flat_values(row["project_candidates"]),
                    "part_candidates": _flat_values(row["part_candidates"]),
                    "warning_codes": " | ".join(item["code"] for item in row["warnings"]),
                }
            )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--manifest-only", action="store_true", help="hash and enumerate without ODA conversion"
    )
    args = parser.parse_args(argv)
    report = write_corpus_report(args.input_dir, args.output_dir, convert=not args.manifest_only)
    print(json.dumps(report["aggregate"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
