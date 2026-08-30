from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from ezdxf.entities import DXFEntity
from ezdxf.math import Matrix44
from ezdxf.transform import copies

from .contracts import (
    DevelopedPlate,
    PLBatchResult,
    PLCompilation,
    PLItemResult,
    PLMetadata,
    PLSourceContext,
    PLSplitError,
)
from .development import K_FACTOR, calculate_target, transform_outline
from .geometry import analyze_geometry
from .longitudinal import (
    analyze_longitudinal_outline,
    analyze_uniform_longitudinal_outline,
)
from .source import discover_input_files, extract_metadata, load_source_contexts
from .writer import write_pl_dxf

REPORT_SCHEMA = "steel-dxf-split-pl-report/2"
REPORT_FILENAME = "pl_split_report.json"
MAX_UNIFORM_FALLBACK_RATIO = 0.001


def _allows_uniform_fallback(total_extension_mm: float, projection_mm: float) -> bool:
    return (
        total_extension_mm <= 0.1 + 1e-9
        or total_extension_mm / projection_mm <= MAX_UNIFORM_FALLBACK_RATIO + 1e-12
    )


def compile_context(
    context: PLSourceContext,
    output_path: str | Path,
) -> PLCompilation:
    metadata = extract_metadata(context)
    outline, section = analyze_geometry(context, metadata)
    flat_plate = section is None
    k_length_mm = (
        outline.projection_length_mm if flat_plate else section.k_length_mm
    )
    target = calculate_target(
        projection_length_mm=outline.projection_length_mm,
        k_length_mm=k_length_mm,
        bom_length_mm=metadata.bom_length_mm,
    )
    if flat_plate or outline.cutout_entity_groups:
        longitudinal = analyze_uniform_longitudinal_outline(
            outline.outer_entities,
            outline.polygon,
        )
    else:
        try:
            longitudinal = analyze_longitudinal_outline(
                outline.outer_entities,
                outline.polygon,
                thickness_mm=metadata.thickness_mm,
            )
        except PLSplitError:
            if not _allows_uniform_fallback(
                target.total_extension_mm,
                target.projection_length_mm,
            ):
                raise
            longitudinal = analyze_uniform_longitudinal_outline(
                outline.outer_entities,
                outline.polygon,
            )
    try:
        transformed, metrics = transform_outline(
            outline.outer_entities,
            longitudinal=longitudinal,
            projection_length_mm=outline.projection_length_mm,
            k_length_mm=k_length_mm,
            bom_length_mm=metadata.bom_length_mm,
            anchor_x_mm=outline.anchor_x_mm,
            k_factor=None if flat_plate else K_FACTOR,
        )
    except PLSplitError:
        if (
            not _allows_uniform_fallback(
                target.total_extension_mm,
                target.projection_length_mm,
            )
            or longitudinal.selection_reason == "uniform_projection_fallback"
        ):
            raise
        longitudinal = analyze_uniform_longitudinal_outline(
            outline.outer_entities,
            outline.polygon,
        )
        transformed, metrics = transform_outline(
            outline.outer_entities,
            longitudinal=longitudinal,
            projection_length_mm=outline.projection_length_mm,
            k_length_mm=k_length_mm,
            bom_length_mm=metadata.bom_length_mm,
            anchor_x_mm=outline.anchor_x_mm,
            k_factor=None if flat_plate else K_FACTOR,
        )
    transformed_cutouts: list[tuple[DXFEntity, ...]] = []
    if outline.cutout_entity_groups:
        scale = target.target_length_mm / target.projection_length_mm
        matrix = Matrix44.chain(
            Matrix44.translate(-outline.anchor_x_mm, 0.0, 0.0),
            Matrix44.scale(scale, 1.0, 1.0),
            Matrix44.translate(outline.anchor_x_mm, 0.0, 0.0),
        )
        for group in outline.cutout_entity_groups:
            log, result = copies(group, matrix)
            if len(log) or len(result) != len(group):
                raise PLSplitError(
                    "TRANSFORM_FAILED",
                    "PL孔槽无法随主视图完整执行0.1 mm内等比拉伸。",
                )
            transformed_cutouts.append(tuple(result))
    developed = DevelopedPlate(
        metadata=metadata,
        outline=outline,
        section=section,
        longitudinal=longitudinal,
        transformed_entities=transformed,
        metrics=metrics,
        transformed_cutout_entity_groups=tuple(transformed_cutouts),
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
    section = developed.section
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
            "plate_mode": "flat" if section is None else "bent",
            "main_candidate_count": developed.outline.candidate_count,
            "section_candidate_count": 0 if section is None else section.candidate_count,
            "main_source_handles": list(developed.outline.source_handles),
            "section_source_handles": [] if section is None else list(section.source_handles),
        },
        "lengths": {
            "projection_mm": metrics.projection_length_mm,
            "section_area_mm2": None if section is None else section.polygon.area,
            "section_thickness_mm": (
                None if section is None else developed.metadata.thickness_mm
            ),
            "k_factor": metrics.k_factor,
            "k_proof_method": None if section is None else section.proof_method,
            "k_length_mm": None if section is None else metrics.k_length_mm,
            "bom_mm": metrics.bom_length_mm,
            "raw_mm": metrics.raw_length_mm,
            "target_mm": metrics.target_length_mm,
            "total_extension_mm": metrics.total_extension_mm,
        },
        "geometry": {
            "source_width_mm": developed.outline.width_mm,
            "source_anchor_x_mm": developed.outline.anchor_x_mm,
        },
        "transform": {
            "carrier_interval_indices": list(metrics.carrier_interval_indices),
            "selection_reason": developed.longitudinal.selection_reason,
            "total_extension_mm": metrics.total_extension_mm,
            "carrier_upper_scale_x": metrics.carrier_upper_scale_x,
            "carrier_lower_scale_x": metrics.carrier_lower_scale_x,
            "intervals": [
                {
                    "index": interval.index,
                    "source_upper_span_mm": interval.source_upper_span_mm,
                    "source_lower_span_mm": interval.source_lower_span_mm,
                    "output_upper_span_mm": interval.output_upper_span_mm,
                    "output_lower_span_mm": interval.output_lower_span_mm,
                    "downstream_shift_mm": interval.downstream_shift_mm,
                    "is_carrier": interval.is_carrier,
                }
                for interval in metrics.intervals
            ],
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
        context
        for input_file in input_files
        for context in load_source_contexts(input_file)
    )
    metadata_by_context: dict[int, PLMetadata] = {}
    early_rejections: dict[int, PLItemResult] = {}
    for index, context in enumerate(contexts):
        try:
            metadata_by_context[index] = extract_metadata(context)
        except PLSplitError as error:
            early_rejections[index] = _rejected_item(context, error)
    counts = Counter(
        metadata.part_number.casefold() for metadata in metadata_by_context.values()
    )
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
