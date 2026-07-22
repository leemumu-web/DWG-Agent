"""Stable response projections for Excel Final HTTP adapters."""

from __future__ import annotations

from collections.abc import Iterable

from app.modules.excel_processing.models import (
    ExcelFinalBatch,
    ExcelFinalComponent,
    ExcelFinalPart,
)
from app.modules.jobs.interface import Job


def _json_number(value: object) -> float | None:
    return float(value) if value is not None else None


def batch_summary(batch: ExcelFinalBatch) -> dict[str, object]:
    return {
        "batch_id": batch.id,
        "job_id": batch.job_id,
        "file_id": batch.file_id,
        "source_type": batch.source_type,
        "source_name": batch.source_name,
        "part_count": batch.part_count,
        "component_count": batch.component_count,
        "total_net_weight": _json_number(batch.total_net_weight),
        "total_gross_weight": _json_number(batch.total_gross_weight),
        "quality_status": batch.quality_status,
        "warning_count": batch.warning_count,
        "severe_warning_count": batch.severe_warning_count,
        "report_summary": batch.report_summary,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
    }


def completed_batch_summary(batch: ExcelFinalBatch) -> dict[str, object]:
    return {
        "batch_id": batch.id,
        "source_type": batch.source_type,
        "source_name": batch.source_name,
        "part_count": batch.part_count,
        "component_count": batch.component_count,
        "total_net_weight": _json_number(batch.total_net_weight),
        "total_gross_weight": _json_number(batch.total_gross_weight),
        "quality_status": batch.quality_status,
        "warning_count": batch.warning_count,
        "severe_warning_count": batch.severe_warning_count,
        "report_summary": batch.report_summary,
    }


def process_status(
    job: Job,
    *,
    batch: ExcelFinalBatch | None,
    result_file_id: int | None,
) -> dict[str, object]:
    return {
        "job_id": job.id,
        "status": job.status,
        "progress": job.progress,
        "pipeline": job.pipeline,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "batch": completed_batch_summary(batch) if batch is not None else None,
        "result_file_id": result_file_id,
    }


def batch_detail(
    batch: ExcelFinalBatch,
    *,
    material_stats: Iterable[tuple[str, int, float | None]],
    spec_stats: Iterable[tuple[str, int]],
) -> dict[str, object]:
    return {
        **batch_summary(batch),
        "material_breakdown": [
            {
                "material": material,
                "count": count,
                "total_net_weight": _json_number(weight),
            }
            for material, count, weight in material_stats
        ],
        "top_specs": [{"spec": spec, "count": count} for spec, count in spec_stats],
    }


def part_catalog_item(part: ExcelFinalPart) -> dict[str, object]:
    return {
        "id": part.id,
        "seq": part.seq,
        "import_component_no": part.import_component_no,
        "import_part_no": part.import_part_no,
        "source_batch": part.source_batch,
        "team": part.team,
        "original_qty": _json_number(part.original_qty),
        "component_no": part.component_no,
        "component_qty": part.component_qty,
        "part_type": part.part_type,
        "part_no": part.part_no,
        "profile_spec": part.profile_spec,
        "spec": part.spec,
        "width": _json_number(part.width),
        "length": _json_number(part.length),
        "cut_length": _json_number(part.cut_length),
        "material": part.material,
        "qty": _json_number(part.qty),
        "total_qty": _json_number(part.total_qty),
        "total_length": _json_number(part.total_length),
        "density": _json_number(part.density),
        "density_source": part.density_source,
        "theo_unit_weight": _json_number(part.theo_unit_weight),
        "theo_total_weight": _json_number(part.theo_total_weight),
        "material_utilization": _json_number(part.material_utilization),
        "weight_validation": part.weight_validation,
        "net_unit_weight": _json_number(part.net_unit_weight),
        "net_total_weight": _json_number(part.net_total_weight),
        "table_net_weight": _json_number(part.table_net_weight),
        "gross_unit_weight": _json_number(part.gross_unit_weight),
        "gross_total_weight": _json_number(part.gross_total_weight),
        "table_gross_weight": _json_number(part.table_gross_weight),
        "surface_area": _json_number(part.surface_area),
        "total_surface_area": _json_number(part.total_surface_area),
    }


def part_detail(part: ExcelFinalPart) -> dict[str, object]:
    return {
        "id": part.id,
        "batch_id": part.batch_id,
        "seq": part.seq,
        "import_component_no": part.import_component_no,
        "import_part_no": part.import_part_no,
        "source_batch": part.source_batch,
        "team": part.team,
        "original_qty": _json_number(part.original_qty),
        "component_no": part.component_no,
        "component_qty": part.component_qty,
        "part_type": part.part_type,
        "part_no": part.part_no,
        "profile_spec": part.profile_spec,
        "spec": part.spec,
        "width": _json_number(part.width),
        "length": _json_number(part.length),
        "left_inset": _json_number(part.left_inset),
        "right_inset": _json_number(part.right_inset),
        "cut_length": _json_number(part.cut_length),
        "material": part.material,
        "qty": _json_number(part.qty),
        "total_qty": _json_number(part.total_qty),
        "total_length": _json_number(part.total_length),
        "density": _json_number(part.density),
        "density_source": part.density_source,
        "theo_unit_weight": _json_number(part.theo_unit_weight),
        "theo_total_weight": _json_number(part.theo_total_weight),
        "material_utilization": _json_number(part.material_utilization),
        "weight_validation": part.weight_validation,
        "net_unit_weight": _json_number(part.net_unit_weight),
        "net_total_weight": _json_number(part.net_total_weight),
        "table_net_weight": _json_number(part.table_net_weight),
        "gross_unit_weight": _json_number(part.gross_unit_weight),
        "gross_total_weight": _json_number(part.gross_total_weight),
        "table_gross_weight": _json_number(part.table_gross_weight),
        "surface_area": _json_number(part.surface_area),
        "total_surface_area": _json_number(part.total_surface_area),
        "created_at": part.created_at.isoformat() if part.created_at else None,
    }


def part_search_item(part: ExcelFinalPart) -> dict[str, object]:
    return {
        "id": part.id,
        "batch_id": part.batch_id,
        "seq": part.seq,
        "import_component_no": part.import_component_no,
        "import_part_no": part.import_part_no,
        "source_batch": part.source_batch,
        "team": part.team,
        "original_qty": _json_number(part.original_qty),
        "component_no": part.component_no,
        "part_type": part.part_type,
        "part_no": part.part_no,
        "spec": part.spec,
        "width": _json_number(part.width),
        "length": _json_number(part.length),
        "material": part.material,
        "qty": _json_number(part.qty),
        "net_total_weight": _json_number(part.net_total_weight),
        "theo_total_weight": _json_number(part.theo_total_weight),
        "density_source": part.density_source,
        "material_utilization": _json_number(part.material_utilization),
        "weight_validation": part.weight_validation,
    }


def component_item(component: ExcelFinalComponent) -> dict[str, object]:
    return {
        "id": component.id,
        "component_no": component.component_no,
        "component_qty": component.component_qty,
        "total_weight": _json_number(component.total_weight),
    }


__all__ = [
    "batch_detail",
    "batch_summary",
    "component_item",
    "part_catalog_item",
    "part_detail",
    "part_search_item",
    "process_status",
]
