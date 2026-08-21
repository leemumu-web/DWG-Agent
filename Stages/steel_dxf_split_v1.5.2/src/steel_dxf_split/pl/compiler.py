from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import (
    DevelopedPlate,
    PLBatchResult,
    PLCompilation,
    PLItemResult,
    PLMetadata,
    PLSourceContext,
    PLSplitError,
)
from .development import transform_outline
from .geometry import analyze_geometry
from .longitudinal import analyze_longitudinal_outline
from .source import discover_input_files, extract_metadata, load_source_contexts
from .writer import write_pl_dxf

REPORT_SCHEMA = "steel-dxf-split-pl-report/1"
REPORT_FILENAME = "pl_split_report.json"


def compile_context(
    context: PLSourceContext,
    output_path: str | Path,
) -> PLCompilation:
    metadata = extract_metadata(context)
    outline, section = analyze_geometry(context, metadata)
    longitudinal = analyze_longitudinal_outline(
        outline.outer_entities,
        outline.polygon,
        thickness_mm=metadata.thickness_mm,
    )
    transformed, metrics = transform_outline(
        outline.outer_entities,
        longitudinal=longitudinal,
        projection_length_mm=outline.projection_length_mm,
        k_length_mm=section.k_length_mm,
        bom_length_mm=metadata.bom_length_mm,
        anchor_x_mm=outline.anchor_x_mm,
    )
    developed = DevelopedPlate(
        metadata=metadata,
        outline=outline,
        section=section,
        longitudinal=longitudinal,
        transformed_entities=transformed,
        metrics=metrics,
    )
    return PLCompilation(
        developed=developed,
        write_result=write_pl_dxf(developed, output_path),
    )


def _rejected_item(
    context: PLSourceContext,
    error: PLSplitError,
    metadata: PLMetadata | None = None,
) -> PLItemResult:
    return PLItemResult(
        status="rejected",
        source_path=context.source_path,
        context_id=context.context_id,
        part_number=None if metadata is None else metadata.part_number,
        output_path=None,
        compilation=None,
        error_code=error.code,
        error_message_zh=error.message_zh,
    )


def _success_payload(item: PLItemResult) -> dict[str, object]:
    compilation = item.compilation
    if compilation is None or item.output_path is None:
        raise ValueError("success item is missing compilation evidence")
    developed = compilation.developed
    metrics = developed.metrics
    write = compilation.write_result
    return {
        "status": "success",
        "source": str(item.source_path),
        "context_id": item.context_id,
        "part_number": item.part_number,
        "metadata": {
            "thickness_mm": developed.metadata.thickness_mm,
            "width_mm": developed.metadata.width_mm,
            "bom_length_mm": developed.metadata.bom_length_mm,
        },
        "evidence": {
            "main_candidate_count": developed.outline.candidate_count,
            "section_candidate_count": developed.section.candidate_count,
            "main_source_handles": list(developed.outline.source_handles),
            "section_source_handles": list(developed.section.source_handles),
        },
        "lengths": {
            "projection_mm": metrics.projection_length_mm,
            "section_area_mm2": developed.section.polygon.area,
            "section_thickness_mm": developed.metadata.thickness_mm,
            "k_factor": metrics.k_factor,
            "k_proof_method": developed.section.proof_method,
            "k_length_mm": metrics.k_length_mm,
            "bom_mm": metrics.bom_length_mm,
            "raw_mm": metrics.raw_length_mm,
            "target_mm": metrics.target_length_mm,
        },
        "geometry": {
            "source_width_mm": developed.outline.width_mm,
            "source_anchor_x_mm": developed.outline.anchor_x_mm,
        },
        "transform": {
            "scale_x": metrics.carrier_upper_scale_x,
            "anchor_x_mm": metrics.anchor_x_mm,
        },
        "output": {
            "path": str(item.output_path),
            "label": write.label,
            "min_x_mm": write.min_x_mm,
            "length_mm": write.length_mm,
            "width_mm": write.width_mm,
            "entity_type_counts": dict(write.entity_type_counts),
            "audit_error_count": write.audit_error_count,
            "shapely_closed_valid": True,
        },
    }


def _item_payload(item: PLItemResult) -> dict[str, object]:
    if item.status == "success":
        return _success_payload(item)
    return {
        "status": "rejected",
        "source": str(item.source_path),
        "context_id": item.context_id,
        "part_number": item.part_number,
        "error": {
            "code": item.error_code,
            "message_zh": item.error_message_zh,
        },
    }


def batch_payload(batch: PLBatchResult) -> dict[str, object]:
    return {
        "schema": REPORT_SCHEMA,
        "input": str(batch.input_path),
        "output_dir": str(batch.output_dir),
        "report": str(batch.report_path),
        "success_count": batch.success_count,
        "rejected_count": batch.rejected_count,
        "exit_code": batch.exit_code,
        "items": [_item_payload(item) for item in batch.items],
    }


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def split_pl(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> PLBatchResult:
    source = Path(input_path).expanduser().resolve(strict=False)
    output = Path(output_dir).expanduser().resolve(strict=False)
    input_files = discover_input_files(source, output)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / REPORT_FILENAME
    if report_path.exists() and not overwrite:
        raise PLSplitError(
            "OUTPUT_REPORT_EXISTS",
            f"报告已存在，未启用 --overwrite：{report_path}",
        )
    contexts = tuple(
        context for input_file in input_files for context in load_source_contexts(input_file)
    )
    metadata_by_context: dict[int, PLMetadata] = {}
    early_rejections: dict[int, PLItemResult] = {}
    for index, context in enumerate(contexts):
        try:
            metadata_by_context[index] = extract_metadata(context)
        except PLSplitError as error:
            early_rejections[index] = _rejected_item(context, error)
    counts = Counter(metadata.part_number.casefold() for metadata in metadata_by_context.values())
    duplicate_keys = {part for part, count in counts.items() if count > 1}
    items: list[PLItemResult] = []
    with TemporaryDirectory(prefix=".pl-split-", dir=output) as temporary:
        temporary_dir = Path(temporary)
        for index, context in enumerate(contexts):
            if index in early_rejections:
                items.append(early_rejections[index])
                continue
            metadata = metadata_by_context[index]
            if metadata.part_number.casefold() in duplicate_keys:
                items.append(
                    _rejected_item(
                        context,
                        PLSplitError(
                            "DUPLICATE_PART_NUMBER",
                            f"批次内零件号重复：{metadata.part_number}",
                        ),
                        metadata,
                    )
                )
                continue
            final_path = output / f"{metadata.part_number}.dxf"
            if final_path.exists() and not overwrite:
                items.append(
                    _rejected_item(
                        context,
                        PLSplitError(
                            "OUTPUT_EXISTS",
                            f"结果已存在，未启用 --overwrite：{final_path}",
                        ),
                        metadata,
                    )
                )
                continue
            temporary_path = temporary_dir / final_path.name
            try:
                compilation = compile_context(context, temporary_path)
            except PLSplitError as error:
                items.append(_rejected_item(context, error, metadata))
                continue
            os.replace(temporary_path, final_path)
            published = replace(
                compilation,
                write_result=replace(compilation.write_result, output_path=final_path),
            )
            items.append(
                PLItemResult(
                    status="success",
                    source_path=context.source_path,
                    context_id=context.context_id,
                    part_number=metadata.part_number,
                    output_path=final_path,
                    compilation=published,
                    error_code=None,
                    error_message_zh=None,
                )
            )
        batch = PLBatchResult(
            input_path=source,
            output_dir=output,
            report_path=report_path,
            items=tuple(items),
        )
        temporary_report = temporary_dir / REPORT_FILENAME
        _write_report(temporary_report, batch_payload(batch))
        os.replace(temporary_report, report_path)
    return batch
