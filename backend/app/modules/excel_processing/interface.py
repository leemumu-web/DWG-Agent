"""Supported cross-domain boundary for Excel Final processing."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.modules.excel_processing.models import (
    ExcelFinalBatch,
    ExcelFinalComponent,
    ExcelFinalPart,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def cleanup_excel_processing_rows(db: Session, job_ids: Iterable[int]) -> int:
    """Delete Excel-owned provisional rows for cancelled or abandoned Jobs."""
    from app.modules.excel_processing.persistence import cleanup_excel_processing_rows as cleanup

    return cleanup(db, job_ids)


def run_excel_final_processing(job_id: int, **kwargs) -> None:
    """Execute one attempt of the implemented Excel Final pipeline."""
    from app.modules.excel_processing.execution import run_excel_final_processing as run

    run(job_id, **kwargs)


def enqueue_excel_final_job(job_id: int, attempt: int) -> str:
    """Enqueue an Excel Final attempt while hiding the concrete Celery module."""
    from app.modules.excel_processing.tasks import process_excel_final_task

    return str(process_excel_final_task.delay(job_id, attempt).id)


__all__ = [
    "ExcelFinalBatch",
    "ExcelFinalComponent",
    "ExcelFinalPart",
    "cleanup_excel_processing_rows",
    "enqueue_excel_final_job",
    "run_excel_final_processing",
]
