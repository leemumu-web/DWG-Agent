from __future__ import annotations

from collections import Counter
import csv
import json
import os
from pathlib import Path
import shutil
import time
from uuid import uuid4

from .classify import classify_file
from .model import BatchSummary, ClassificationResult, Disposition
from .preprocess import preprocess_dxf_filenames


REPORT_SCHEMA = "STEEL-DXF-CLASSIFICATION-1.2"


def _project_name(source: Path) -> str:
    if not source.is_dir():
        raise ValueError(f"input is not a directory: {source}")
    if not source.name.endswith("_dxf") or source.name == "_dxf":
        raise ValueError("input directory name must match <项目名称>_dxf")
    return source.name[:-4]


def _route_name(project: str, result: ClassificationResult) -> str:
    if result.disposition is Disposition.CLASSIFIED:
        assert result.part_type is not None
        label = result.part_type
    elif result.disposition is Disposition.REVIEW_REQUIRED:
        label = "待确认"
    else:
        label = "无法读取"
    return f"{project}_{label}_dxf"


def _existing_outputs(parent: Path, source: Path, project: str) -> list[Path]:
    prefix = f"{project}_"
    outputs = [
        path
        for path in parent.iterdir()
        if path != source
        and path.is_dir()
        and path.name.startswith(prefix)
        and path.name.endswith("_dxf")
    ]
    for name in (f"{project}_分类报告.json", f"{project}_分类清单.csv"):
        path = parent / name
        if path.exists():
            outputs.append(path)
    return sorted(outputs, key=lambda path: path.name)


def _result_payload(
    result: ClassificationResult,
    output_directory: str,
) -> dict[str, object]:
    payload = result.to_dict()
    payload["output_directory"] = output_directory
    return payload


def _write_reports(
    staging: Path,
    project: str,
    summary: BatchSummary,
    routed: list[tuple[ClassificationResult, str]],
) -> None:
    payload = {
        "schema": REPORT_SCHEMA,
        "summary": summary.to_dict(),
        "results": [_result_payload(result, route) for result, route in routed],
    }
    report_path = staging / f"{project}_分类报告.json"
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    csv_path = staging / f"{project}_分类清单.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        fieldnames = [
            "文件名",
            "处置",
            "零件类型",
            "规格原文",
            "规格规范值",
            "类型注册状态",
            "类型来源",
            "下一阶段可用",
            "诊断码",
            "输出目录",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for result, route in routed:
            winner = result.candidates[0] if result.candidates else None
            writer.writerow(
                {
                    "文件名": result.source_name,
                    "处置": result.disposition.value,
                    "零件类型": result.part_type or "",
                    "规格原文": result.profile_raw or "",
                    "规格规范值": result.profile_normalized or "",
                    "类型注册状态": (
                        winner.profile.catalog_status if winner is not None else ""
                    ),
                    "类型来源": result.type_source or "",
                    "下一阶段可用": "是" if result.next_stage_eligible else "否",
                    "诊断码": ";".join(result.diagnostics),
                    "输出目录": route,
                }
            )


def _promote(staging: Path, parent: Path, existing: list[Path]) -> None:
    """把 staging 目录整体提权为正式输出（备份-替换-回滚协议）。

    崩溃一致性：旧结果先整体移入 ``.backup`` 子目录，再逐个 ``os.replace``
    提权新结果——保证正式目录要么全新、要么全旧，绝不出现新旧混合。
    异常时按「先删已提权项、再还原备份」的顺序恢复；崩溃后残留的
    ``.backup`` 目录可据此识别并人工恢复。注意本函数不做 fsync，
    崩溃窗口内文件系统缓存可能丢失（与 pipeline._promote_task_directory
    的 fsync 协议不同）。
    """
    backup = parent / f".{staging.name}.backup"
    promoted: list[Path] = []
    try:
        if existing:
            backup.mkdir()
            for path in existing:
                os.replace(path, backup / path.name)
        for staged in sorted(staging.iterdir(), key=lambda path: path.name):
            destination = parent / staged.name
            os.replace(staged, destination)
            promoted.append(destination)
    except Exception:
        for path in reversed(promoted):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        if backup.exists():
            for path in backup.iterdir():
                os.replace(path, parent / path.name)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(backup, ignore_errors=True)


def classify_directory(
    input_directory: str | Path,
    *,
    overwrite: bool = False,
) -> BatchSummary:
    started = time.perf_counter()
    source = Path(input_directory).resolve()
    project = _project_name(source)
    parent = source.parent
    existing = _existing_outputs(parent, source, project)
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing)
        raise FileExistsError(f"classification outputs already exist ({names}); use --overwrite")

    inputs = list(preprocess_dxf_filenames(source))
    results = tuple(classify_file(path) for path in inputs)
    staging = parent / f".{project}.classifier-staging-{uuid4().hex}"
    staging.mkdir()
    routed: list[tuple[ClassificationResult, str]] = []
    try:
        for path, result in zip(inputs, results, strict=True):
            route = _route_name(project, result)
            output_directory = staging / route
            output_directory.mkdir(exist_ok=True)
            shutil.copy2(path, output_directory / path.name)
            routed.append((result, route))

        type_counts = Counter(
            result.part_type
            for result in results
            if result.disposition is Disposition.CLASSIFIED and result.part_type is not None
        )
        output_directories = tuple(sorted({route for _, route in routed}))
        summary = BatchSummary(
            project_name=project,
            input_directory=str(source),
            input_count=len(inputs),
            classified_count=sum(
                result.disposition is Disposition.CLASSIFIED for result in results
            ),
            review_required_count=sum(
                result.disposition is Disposition.REVIEW_REQUIRED for result in results
            ),
            unreadable_count=sum(
                result.disposition is Disposition.UNREADABLE for result in results
            ),
            type_counts=dict(type_counts),
            output_directories=output_directories,
            elapsed_seconds=time.perf_counter() - started,
            results=results,
        )
        copied_count = sum(
            1
            for route in output_directories
            for path in (staging / route).iterdir()
            if path.is_file()
        )
        if copied_count != len(inputs):
            raise RuntimeError(
                f"staging copy count mismatch: expected {len(inputs)}, got {copied_count}"
            )
        _write_reports(staging, project, summary, routed)
        _promote(staging, parent, existing if overwrite else [])
        return summary
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
