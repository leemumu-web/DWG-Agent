"""Register a generated material workbook in MySQL and object storage."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.modules.cad_processing.dxf_to_excel.contracts import (
    ALGORITHM_VERSION,
    ERROR_CODE_DXF2EXCEL_NO_OUTPUT,
    EXCEL_CONTENT_TYPE,
    EXCEL_EXTENSION,
)
from app.modules.cad_processing.execution import (
    CadProcessingError,
    add_job_step,
    mark_job_failed,
)
from app.modules.files.interface import sanitize_filename, save_bytes_as_file
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    complete_job_attempt,
    make_event,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    JOB_SUCCEEDED,
    STEP_PERSIST_EXCEL,
    TASK_DXF_TO_EXCEL,
)
from app.platform.config.settings import settings
from app.platform.time import business_now

logger = logging.getLogger(__name__)


def persist_excel_extraction_result(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    batch_name: str,
    output_path: Path,
    pipeline_stats: dict[str, Any],
    worker_name: str,
) -> bool:
    """Register the workbook only for the still-active Job attempt."""
    job = db.get(Job, job_id, populate_existing=True)
    if job is None or job.status != JOB_RUNNING or job.attempt != attempt:
        db.rollback()
        return False
    if not output_path.is_file():
        mark_job_failed(
            db,
            job_id,
            attempt,
            CadProcessingError("Excel 输出文件未生成"),
            error_code=ERROR_CODE_DXF2EXCEL_NO_OUTPUT,
            logger=logger,
        )
        return False

    persist_started = business_now()
    excel_bytes = output_path.read_bytes()
    output_basename = sanitize_filename(batch_name)
    excel_file = save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_reports,
        storage_key=f"jobs/{job.id}/{uuid4().hex}{EXCEL_EXTENSION}",
        original_name=f"{output_basename}{EXCEL_EXTENSION}",
        file_ext=EXCEL_EXTENSION,
        content_type=EXCEL_CONTENT_TYPE,
        payload=excel_bytes,
        uploaded_by=job.created_by,
    )
    analysis = AnalysisResult(
        job_id=job.id,
        drawing_id=job.drawing_id,
        result_type=TASK_DXF_TO_EXCEL,
        result_json={
            "source": "dxf2excel",
            "job_id": job.id,
            "task_type": TASK_DXF_TO_EXCEL,
            "batch_name": batch_name,
            **pipeline_stats,
            "excel_file_id": excel_file.id,
        },
        confidence=Decimal("1.0000"),
        result_file_id=excel_file.id,
        algorithm_version=ALGORITHM_VERSION,
        tool_version="dxf2excel",
        status="succeeded",
    )
    db.add(analysis)
    db.flush()
    add_job_step(
        db,
        job_id,
        attempt,
        STEP_PERSIST_EXCEL,
        worker_name,
        "succeeded",
        input_json={"excel_size": len(excel_bytes)},
        output_json={
            "excel_file_id": excel_file.id,
            "analysis_result_id": analysis.id,
            "tables_found": pipeline_stats["tables_found"],
            "data_rows": pipeline_stats["data_rows"],
            "warnings_count": pipeline_stats["warnings_count"],
        },
        started_at=persist_started,
    )
    completed_job = complete_job_attempt(
        db,
        job_id,
        attempt=attempt,
        event=make_event(
            type_="done",
            status=JOB_SUCCEEDED,
            progress=100,
            step_name=STEP_PERSIST_EXCEL,
            message=f"Excel 已生成: {pipeline_stats['tables_found']} 张表, "
            f"{pipeline_stats['data_rows']} 行数据, "
            f"{pipeline_stats['warnings_count']} 个警告",
            excel_file_id=excel_file.id,
            excel_name=f"{output_basename}{EXCEL_EXTENSION}",
            tables_found=pipeline_stats["tables_found"],
            data_rows=pipeline_stats["data_rows"],
            warnings_count=pipeline_stats["warnings_count"],
        ),
    )
    return completed_job is not None
