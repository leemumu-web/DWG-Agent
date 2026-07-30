"""Supported cross-domain boundary for Excel Final processing."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.modules.excel_processing.models import (
    ExcelFinalBatch,
    ExcelFinalComponent,
    ExcelFinalPart,
)
from app.modules.excel_processing.schemas import ExcelInputFailure, ExcelStage1Inspection
from app.modules.excel_processing.stage_adapter import (
    ExcelFinalInputError,
    ExcelFinalProcessError,
    ExcelFinalUnavailableError,
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


def run_excel_stage2_processing(job_id: int, **kwargs) -> None:
    """Execute one attempt of the isolated BH Reader and Excel Stage2 pipeline."""
    from app.modules.excel_processing.stage2_execution import (
        run_excel_stage2_processing as run,
    )

    run(job_id, **kwargs)


def enqueue_excel_stage2_job(job_id: int, attempt: int) -> str:
    """Enqueue one Excel Stage2 attempt through its stable task boundary."""
    from app.modules.excel_processing.tasks import process_excel_stage2_task

    return str(process_excel_stage2_task.delay(job_id, attempt).id)


def inspect_excel_stage1_bytes(
    *,
    file_name: str,
    payload: bytes,
    expected_sha256: str | None = None,
) -> ExcelStage1Inspection:
    """Validate storage bytes through the single Stage-owned input contract."""
    from app.modules.excel_processing.stage_adapter import inspect_excel_stage1_bytes as inspect

    return inspect(
        file_name=file_name,
        payload=payload,
        expected_sha256=expected_sha256,
    )


__all__ = [
    "ExcelFinalBatch",
    "ExcelFinalComponent",
    "ExcelFinalInputError",
    "ExcelFinalPart",
    "ExcelFinalProcessError",
    "ExcelFinalUnavailableError",
    "ExcelInputFailure",
    "ExcelStage1Inspection",
    "cleanup_excel_processing_rows",
    "enqueue_excel_final_job",
    "enqueue_excel_stage2_job",
    "inspect_excel_stage1_bytes",
    "run_excel_final_processing",
    "run_excel_stage2_processing",
]
