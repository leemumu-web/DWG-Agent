from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
import traceback
from typing import Any, Sequence

from .bh_trace import InMemoryTraceObserver, STAGE_REGISTRY, TraceShape
from .layered_artifacts import ArtifactItem, LayeredArchive
from .layered_pipeline import inspect_bh_pair
from .layered_scene import scene_from_event
from .layered_site import build_site, validate_site_links
from .process_control import IsolatedProcessResult, run_isolated_process


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steel-dxf-inspect",
        description="Generate complete layered JSON/DXF/SVG evidence for BH DXF pairs.",
    )
    parser.add_argument("inputs", nargs="*", type=Path, help="*_拆板前.dxf inputs")
    parser.add_argument("--reference-dir", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--input", dest="worker_input", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--reference", dest="worker_reference", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--result", dest="worker_result", type=Path, help=argparse.SUPPRESS)
    return parser


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _worker(args: argparse.Namespace) -> int:
    if args.worker_input is None or args.worker_reference is None:
        raise ValueError("Worker mode requires --input and --reference")
    result_path = args.worker_result or args.output_root / "worker_result.json"
    try:
        result = inspect_bh_pair(
            args.worker_input,
            args.worker_reference,
            args.output_root,
        )
        _write_json_atomic(result_path, result.to_manifest())
        return 0 if result.ok else 2
    except Exception:
        traceback.print_exc()
        return 2


def _validate_output_root(output_root: Path, inputs: list[Path]) -> Path:
    resolved = output_root.expanduser().resolve()
    protected = {
        Path("/").resolve(),
        Path.home().resolve(),
        Path.cwd().resolve(),
    }
    if resolved in protected:
        raise ValueError(f"Refusing unsafe output root: {resolved}")
    for source in inputs:
        source_resolved = source.resolve()
        if source_resolved == resolved or source_resolved.is_relative_to(resolved):
            raise ValueError(f"Output root contains an input DXF: {resolved}")
    return resolved


def _sample_id(input_path: Path) -> str:
    suffix = "_拆板前.dxf"
    if not input_path.name.endswith(suffix):
        raise ValueError(f"Input is not a {suffix} file: {input_path}")
    return input_path.name.removesuffix(suffix)


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": "STEEL-DXF-LAYERED-CORPUS-1.0",
            "samples": [],
            "run_failures": [],
            "corpus_artifacts": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "STEEL-DXF-LAYERED-CORPUS-1.0":
        raise ValueError(f"Unsupported existing manifest schema: {path}")
    return payload


def _replace_sample(manifest: dict[str, Any], sample: dict[str, Any]) -> None:
    manifest["samples"] = [
        item for item in manifest.get("samples", []) if item.get("sample_id") != sample["sample_id"]
    ]
    manifest["samples"].append(sample)
    manifest["samples"].sort(key=lambda item: str(item["sample_id"]))


def _remove_sample_files(root: Path, sample_id: str) -> None:
    for format_name in ("dxf", "svg"):
        for category in ("intermediate", "final", "reference", "comparison"):
            shutil.rmtree(root / format_name / category / sample_id, ignore_errors=True)
    shutil.rmtree(root / "json" / sample_id, ignore_errors=True)
    shutil.rmtree(root / "site" / "samples" / sample_id, ignore_errors=True)


def _merge_worker_output(worker_root: Path, output_root: Path) -> None:
    for directory in ("dxf", "svg", "json"):
        source_root = worker_root / directory
        if not source_root.exists():
            continue
        for source in sorted(source_root.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(worker_root)
            target = output_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)


def _failure_sample(sample_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "ok": False,
        "stage_status": {
            stage.stage_id: "failed"
            for stage in STAGE_REGISTRY
            if stage.stage_id != "13_corpus_summary"
        },
        "artifacts": [],
        "error": {"code": code, "message": message},
    }


def _worker_process_timing(
    completed: IsolatedProcessResult,
    timeout_seconds: float,
) -> dict[str, object]:
    active_seconds = (
        completed.duration_seconds
        if completed.active_supervision_seconds is None
        else completed.active_supervision_seconds
    )
    return {
        "clock": "time.perf_counter",
        "measurement": "monotonic_wall_clock",
        "timeout_basis": "active_supervision",
        "timeout_budget_seconds": timeout_seconds,
        "wall_seconds": completed.duration_seconds,
        "active_supervision_seconds": active_seconds,
        "unbudgeted_wall_seconds": completed.unbudgeted_wall_seconds,
    }


def _corpus_shapes(samples: list[dict[str, Any]]) -> tuple[TraceShape, ...]:
    shapes: list[TraceShape] = []
    stages = list(STAGE_REGISTRY)
    for row, sample in enumerate(samples):
        sample_id = str(sample["sample_id"])
        shapes.append(
            TraceShape(
                f"sample-label-{row:03d}",
                "text",
                "annotation",
                ((-5.0, float(-row)),),
                properties={"text": sample_id, "height": 0.28},
            )
        )
        for column, stage in enumerate(stages):
            status = str(
                sample.get("stage_status", {}).get(
                    stage.stage_id,
                    "observed" if stage.stage_id == "13_corpus_summary" else "not_applicable",
                )
            )
            role = "pass" if status in {"observed", "selected"} else "failed" if status == "failed" else "warning"
            x0 = float(column)
            y0 = float(-row)
            shapes.append(
                TraceShape(
                    f"cell-{row:03d}-{column:02d}",
                    "polygon",
                    role,
                    ((x0, y0), (x0 + 0.85, y0), (x0 + 0.85, y0 + 0.75), (x0, y0 + 0.75), (x0, y0)),
                    True,
                    properties={
                        "sample_id": sample_id,
                        "stage_id": stage.stage_id,
                        "status": status,
                    },
                )
            )
    return tuple(shapes)


def _build_corpus_outputs(manifest: dict[str, Any], output_root: Path) -> None:
    for path in (
        output_root / "dxf/corpus",
        output_root / "svg/corpus",
        output_root / "json/corpus",
    ):
        shutil.rmtree(path, ignore_errors=True)
    items = [
        ArtifactItem.from_dict(artifact)
        for sample in manifest.get("samples", [])
        for artifact in sample.get("artifacts", [])
    ]
    archive = LayeredArchive(output_root, items=items)
    samples = sorted(manifest.get("samples", []), key=lambda item: str(item["sample_id"]))
    observer = InMemoryTraceObserver("_corpus")
    event = observer.emit(
        stage_id="13_corpus_summary",
        artifact_id="corpus_stage_matrix",
        status="observed" if all(item.get("ok") for item in samples) else "failed",
        title_zh=f"{len(samples)}×{len(STAGE_REGISTRY)} 语料阶段矩阵",
        summary_zh=f"汇总 {len(samples)} 个样本的阶段状态、产物与监督结果。",
        shapes=_corpus_shapes(samples),
        payload={
            "sample_count": len(samples),
            "passed_sample_count": sum(bool(item.get("ok")) for item in samples),
            "failed_sample_count": sum(not bool(item.get("ok")) for item in samples),
            "stage_count": len(STAGE_REGISTRY),
            "samples": [
                {
                    "sample_id": item["sample_id"],
                    "ok": item.get("ok", False),
                    "stage_status": item.get("stage_status", {}),
                    "artifact_count": len(item.get("artifacts", [])),
                }
                for item in samples
            ],
        },
    )
    corpus_item = archive.write_scene_pair(
        scene_from_event(event),
        category="corpus",
        json_payload=event.to_dict(),
        filename="13-corpus-summary",
    )
    manifest["corpus_artifacts"] = [corpus_item.to_dict()]
    manifest["archive_validation"] = archive.validate().to_dict()


def _parent(args: argparse.Namespace) -> int:
    if not args.inputs:
        raise ValueError("At least one *_拆板前.dxf input is required")
    if args.reference_dir is None:
        raise ValueError("--reference-dir is required")
    inputs = sorted((path.expanduser().resolve() for path in args.inputs), key=_sample_id)
    if len({_sample_id(path) for path in inputs}) != len(inputs):
        raise ValueError("Duplicate input sample IDs are not allowed")
    output_root = _validate_output_root(args.output_root, inputs)
    if args.clean:
        shutil.rmtree(output_root, ignore_errors=True)
    output_root.mkdir(parents=True, exist_ok=True)
    manifest_path = output_root / "manifest.json"
    manifest = _load_manifest(manifest_path)
    manifest["run_failures"] = []
    logs_root = output_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    failed = False

    for input_path in inputs:
        sample_id = _sample_id(input_path)
        reference = args.reference_dir.expanduser().resolve() / f"{sample_id}_拆板后.dxf"
        previous = next(
            (item for item in manifest.get("samples", []) if item.get("sample_id") == sample_id),
            None,
        )
        if not reference.exists():
            failure = _failure_sample(sample_id, "MISSING_REFERENCE", reference.name)
            if previous is None or not previous.get("ok"):
                _replace_sample(manifest, failure)
            manifest["run_failures"].append(failure["error"] | {"sample_id": sample_id})
            _write_json_atomic(manifest_path, manifest)
            failed = True
            continue

        with TemporaryDirectory(prefix=f"steel-dxf-{sample_id}-", dir=output_root.parent) as directory:
            worker_root = Path(directory)
            result_path = worker_root / "worker_result.json"
            command = [
                sys.executable,
                "-m",
                "steel_dxf_split.layered_cli",
                "--worker",
                "--input",
                str(input_path),
                "--reference",
                str(reference),
                "--output-root",
                str(worker_root),
                "--result",
                str(result_path),
            ]
            completed = run_isolated_process(command, args.timeout_seconds)
            process_timing = _worker_process_timing(
                completed,
                args.timeout_seconds,
            )
            active_seconds = float(
                process_timing["active_supervision_seconds"]
            )
            log = (
                f"command: {' '.join(command)}\n"
                f"exit_code: {completed.returncode}\n"
                f"duration_seconds: {completed.duration_seconds:.6f}\n"
                f"active_supervision_seconds: {active_seconds:.6f}\n"
                "unbudgeted_wall_seconds: "
                f"{completed.unbudgeted_wall_seconds:.6f}\n"
                f"combined_output:\n{completed.output}\n"
            )
            (logs_root / f"{sample_id}.log").write_text(log, encoding="utf-8")
            if completed.timed_out:
                failure = _failure_sample(
                    sample_id,
                    "WORKER_TIMEOUT",
                    f"active={active_seconds:.6f}s; "
                    f"wall={completed.duration_seconds:.6f}s; "
                    "unbudgeted="
                    f"{completed.unbudgeted_wall_seconds:.6f}s",
                )
                failure["worker_process_timing"] = process_timing
                if previous is None or not previous.get("ok"):
                    _replace_sample(manifest, failure)
                manifest["run_failures"].append(failure["error"] | {"sample_id": sample_id})
                _write_json_atomic(manifest_path, manifest)
                failed = True
                continue
            if completed.returncode != 0 or not result_path.exists():
                failure = _failure_sample(
                    sample_id,
                    "WORKER_FAILED",
                    f"exit={completed.returncode}",
                )
                failure["worker_process_timing"] = process_timing
                if previous is None or not previous.get("ok"):
                    _replace_sample(manifest, failure)
                manifest["run_failures"].append(failure["error"] | {"sample_id": sample_id})
                _write_json_atomic(manifest_path, manifest)
                failed = True
                continue
            sample = json.loads(result_path.read_text(encoding="utf-8"))
            sample["worker_process_timing"] = process_timing
            _remove_sample_files(output_root, sample_id)
            _merge_worker_output(worker_root, output_root)
            _replace_sample(manifest, sample)
            _write_json_atomic(manifest_path, manifest)
            if not sample.get("ok"):
                failed = True

    _build_corpus_outputs(manifest, output_root)
    shutil.rmtree(output_root / "site", ignore_errors=True)
    build_site(manifest, output_root / "site")
    site_validation = validate_site_links(output_root / "site", output_root)
    manifest["site_validation"] = site_validation.to_dict()
    _write_json_atomic(manifest_path, manifest)
    if not manifest.get("archive_validation", {}).get("ok") or not site_validation.ok:
        failed = True
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return _worker(args) if args.worker else _parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
