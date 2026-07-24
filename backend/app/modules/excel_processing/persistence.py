"""Owned MySQL mutation boundary for Excel Final relationship rows."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.modules.excel_processing.importers import import_workbook_to_db
from app.modules.excel_processing.models import ExcelFinalBatch, ExcelFinalPart
from app.modules.excel_processing.schemas import BatchImportStats, QualityExpectation


def cleanup_excel_processing_rows(db: Session, job_ids: Iterable[int]) -> int:
    """Delete batches and cascade their part/component rows for the given Jobs."""
    normalized = tuple(dict.fromkeys(job_id for job_id in job_ids if job_id > 0))
    if not normalized:
        return 0
    result = db.execute(
        delete(ExcelFinalBatch).where(ExcelFinalBatch.job_id.in_(normalized))
    )
    return result.rowcount or 0


def replace_batch_for_job(
    db: Session,
    *,
    job_id: int,
    file_id: int,
    source_type: str,
    source_name: str,
) -> ExcelFinalBatch:
    """Replace rows left by an earlier failed or cancelled execution attempt."""
    cleanup_excel_processing_rows(db, (job_id,))
    db.flush()
    batch = ExcelFinalBatch(
        job_id=job_id,
        file_id=file_id,
        source_type=source_type,
        source_name=source_name,
    )
    db.add(batch)
    db.flush()
    return batch


def import_workbook_for_job(
    db: Session,
    *,
    job_id: int,
    file_id: int,
    source_type: str,
    source_name: str,
    output_path: Path,
    expected_quality: QualityExpectation | None = None,
) -> tuple[ExcelFinalBatch, BatchImportStats]:
    """Atomically replace and populate one Job's relationship projection."""
    batch = replace_batch_for_job(
        db,
        job_id=job_id,
        file_id=file_id,
        source_type=source_type,
        source_name=source_name,
    )
    workbook_stats = import_workbook_to_db(db, batch.id, output_path)
    if expected_quality is not None:
        quality_fields = ("quality_status", "warning_count", "severe_warning_count")
        mismatches = [
            field
            for field in quality_fields
            if expected_quality[field] != workbook_stats[field]
        ]
        if mismatches:
            raise ValueError(
                "Excel Final quality summary mismatch for: " + ", ".join(mismatches)
            )
    batch.part_count = workbook_stats["parts_imported"]
    batch.component_count = workbook_stats["components_imported"]
    batch.quality_status = workbook_stats["quality_status"]
    batch.warning_count = workbook_stats["warning_count"]
    batch.severe_warning_count = workbook_stats["severe_warning_count"]
    batch.report_summary = workbook_stats["report_summary"]
    if batch.part_count > 0:
        total_net, total_gross = db.execute(
            select(
                func.sum(ExcelFinalPart.table_net_weight),
                func.sum(ExcelFinalPart.table_gross_weight),
            ).where(ExcelFinalPart.batch_id == batch.id)
        ).one()
        batch.total_net_weight = total_net
        batch.total_gross_weight = total_gross
    stats: BatchImportStats = {
        "batch_id": batch.id,
        **workbook_stats,
        "total_net_weight": (
            float(batch.total_net_weight) if batch.total_net_weight is not None else None
        ),
        "total_gross_weight": (
            float(batch.total_gross_weight)
            if batch.total_gross_weight is not None
            else None
        ),
    }
    return batch, stats


__all__ = [
    "cleanup_excel_processing_rows",
    "import_workbook_for_job",
    "replace_batch_for_job",
]
