"""Attempt-aware orchestration for the implemented Excel Final pipeline.

The orchestration coordinates files, Jobs, the isolated Stage and Excel-owned
relationship persistence. Workbook parsing and subprocess mechanics stay in
their dedicated modules.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.modules.excel_processing.models import ExcelFinalBatch
from app.modules.excel_processing.persistence import (
    cleanup_excel_processing_rows,
    import_workbook_for_job,
)
from app.modules.excel_processing.stage_adapter import (
    ExcelFinalUnavailableError,
    run_excel_final_pipeline,
)
from app.modules.excel_processing.staging import (
    detect_source_format,
    resolve_file_id,
    stage_excel_source,
)
from app.modules.files.interface import sanitize_filename, save_bytes_as_file
from app.modules.jobs.interface import (
    AnalysisResult,
    JobStep,
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    fail_job_attempt,
    make_event,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    PIPELINE_EXCEL_FINAL,
    STEP_DOWNLOAD_EXCEL_SOURCE,
    STEP_IMPORT_PARTS_DB,
    STEP_PERSIST_EXCEL_FINAL,
    STEP_RUN_EXCEL_FINAL,
    TASK_EXCEL_FINAL,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.storage.base import StorageObjectNotFound

logger = logging.getLogger(__name__)

ERROR_CODE_EMPTY_INPUT = "EXCEL_FINAL_EMPTY_INPUT"
ERROR_CODE_PIPELINE_FAILED = "EXCEL_FINAL_PIPELINE_FAILED"
ERROR_CODE_NO_OUTPUT = "EXCEL_FINAL_NO_OUTPUT"
ERROR_CODE_UNAVAILABLE = "EXCEL_FINAL_UNAVAILABLE"
ERROR_CODE_STORAGE_FAILED = "EXCEL_FINAL_STORAGE_FAILED"
ERROR_CODE_NOT_EXCEL = "EXCEL_FINAL_NOT_EXCEL"
ERROR_CODE_INPUT_CONTRACT = "EXCEL_FINAL_INPUT_CONTRACT"
ERROR_CODE_DB_IMPORT_FAILED = "EXCEL_FINAL_DB_IMPORT_FAILED"

_EXCEL_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EXCEL_EXT = ".xlsx"
_ALGORITHM_VERSION = "excel_final"


class AppError(Exception):
    """Internal error carrying a safe, operator-facing message."""


def _exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message:
            return message
    return str(exc) or exc.__class__.__name__


def _mark_job_failed(
    db: Session,
    job_id: int,
    attempt: int,
    exc: Exception,
    error_code: str = ERROR_CODE_PIPELINE_FAILED,
) -> bool:
    """Commit cleanup and failure only if this is still the active attempt."""
    try:
        cleanup_excel_processing_rows(db, (job_id,))
        return (
            fail_job_attempt(
                db,
                job_id,
                attempt=attempt,
                error_code=error_code,
                error_message=_exception_message(exc),
            )
            is not None
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to mark Excel Final job %s as failed", job_id)
        return False


def _add_step(
    db: Session,
    job_id: int,
    attempt: int,
    step_name: str,
    worker_name: str,
    status: str,
    *,
    input_json: dict | None = None,
    output_json: dict | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
) -> None:
    db.add(
        JobStep(
            job_id=job_id,
            attempt=attempt,
            step_name=step_name,
            worker_name=worker_name,
            status=status,
            input_json=input_json,
            output_json=output_json,
            error_message=error_message,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
    )


def _quality_payload(result) -> dict[str, object]:
    return {
        "quality_status": result.quality_status,
        "warning_count": result.warning_count,
        "severe_warning_count": result.severe_warning_count,
        "report_summary": result.report_summary,
    }


def _completion_message(batch: ExcelFinalBatch) -> str:
    message = (
        f"处理完成: {batch.part_count} 个零件, {batch.component_count} 个构件；"
        f"质量={batch.quality_status}, 警告={batch.warning_count}, "
        f"严重={batch.severe_warning_count}"
    )
    summary = batch.report_summary or {}
    categories = summary.get("category_counts", {})
    handbook_misses = 0
    if isinstance(categories, dict):
        handbook_misses = sum(
            int(count)
            for category, count in categories.items()
            if "查无" in str(category) and isinstance(count, int)
        )
    if handbook_misses:
        message += f"；手册查无={handbook_misses}，请查看处理报告"
    return message


def run_excel_final_processing(
    job_id: int,
    worker_name: str = "celery_excel_final",
    expected_attempt: int = 1,
) -> None:
    """Execute Excel source -> Stage -> MySQL projection -> stored result."""
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_EXCEL_FINAL,
            progress=5,
            message="开始处理 Excel",
        )
        if job is None:
            logger.info("Excel Final job %s was not claimable", job_id)
            return
        attempt = job.attempt
        file_id = resolve_file_id(job)
        if file_id is None:
            _mark_job_failed(
                db,
                job_id,
                attempt,
                AppError("ExcelFinal job 缺少 params.file_id"),
                error_code=ERROR_CODE_EMPTY_INPUT,
            )
            return

        with tempfile.TemporaryDirectory(prefix=f"excel_final_job_{job_id}_") as directory:
            work_dir = Path(directory)
            download_started = datetime.now(UTC)
            try:
                source_path, source_file = stage_excel_source(db, file_id, work_dir)
            except FileNotFoundError:
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"文件 {file_id} 不存在"),
                    error_code=ERROR_CODE_EMPTY_INPUT,
                )
                return
            except ValueError as exc:
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(str(exc)),
                    error_code=ERROR_CODE_NOT_EXCEL,
                )
                return
            except StorageObjectNotFound:
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"文件 {file_id} 存储对象缺失"),
                    error_code=ERROR_CODE_STORAGE_FAILED,
                )
                return

            source_stats = {
                "file_id": file_id,
                "original_name": source_file.original_name,
                "size_bytes": source_file.size_bytes,
            }
            _add_step(
                db,
                job_id,
                attempt,
                STEP_DOWNLOAD_EXCEL_SOURCE,
                worker_name,
                "succeeded",
                input_json={"file_id": file_id},
                output_json=source_stats,
                started_at=download_started,
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=15,
                event=make_event(
                    type_="progress",
                    progress=15,
                    step_name=STEP_DOWNLOAD_EXCEL_SOURCE,
                    status=JOB_RUNNING,
                    message=f"已下载: {source_file.original_name}",
                ),
            )
            if job is None:
                return

            try:
                source_format = detect_source_format(source_path)
            except ValueError as exc:
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(str(exc)),
                    error_code=ERROR_CODE_INPUT_CONTRACT,
                )
                return
            logger.info("Detected format for file_id=%s: %s", file_id, source_format)
            output_basename = sanitize_filename(source_file.original_name.rsplit(".", 1)[0])
            output_path = work_dir / f"{output_basename}_处理后.xlsx"
            pipeline_started = datetime.now(UTC)
            try:
                pipeline_result = run_excel_final_pipeline(
                    source_path,
                    output_path,
                    source_format=source_format,
                )
            except ExcelFinalUnavailableError as exc:
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_RUN_EXCEL_FINAL,
                    worker_name,
                    "failed",
                    input_json={"file_id": file_id, "format": source_format},
                    error_message=f"Excel Final Stage 不可用: {exc}",
                    started_at=pipeline_started,
                )
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"Excel Final Stage 不可用: {exc}"),
                    error_code=ERROR_CODE_UNAVAILABLE,
                )
                return
            except Exception as exc:
                logger.exception("Excel Final Stage failed for job %s", job_id)
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_RUN_EXCEL_FINAL,
                    worker_name,
                    "failed",
                    input_json={"file_id": file_id, "format": source_format},
                    error_message=_exception_message(exc),
                    started_at=pipeline_started,
                )
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"流水线处理失败: {exc}"),
                    error_code=ERROR_CODE_PIPELINE_FAILED,
                )
                return

            _add_step(
                db,
                job_id,
                attempt,
                STEP_RUN_EXCEL_FINAL,
                worker_name,
                "succeeded",
                input_json={"file_id": file_id, "format": source_format},
                output_json={
                    "output": str(output_path),
                    **_quality_payload(pipeline_result),
                },
                started_at=pipeline_started,
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=60,
                event=make_event(
                    type_="progress",
                    progress=60,
                    step_name=STEP_RUN_EXCEL_FINAL,
                    status=JOB_RUNNING,
                    message=(
                        f"流水线完成 (format={source_format}, "
                        f"quality={pipeline_result.quality_status})"
                    ),
                    **_quality_payload(pipeline_result),
                ),
            )
            if job is None:
                return
            if not output_path.is_file():
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError("Excel 输出文件未生成"),
                    error_code=ERROR_CODE_NO_OUTPUT,
                )
                return

            import_started = datetime.now(UTC)
            try:
                batch, database_stats = import_workbook_for_job(
                    db,
                    job_id=job.id,
                    file_id=file_id,
                    source_type=source_format,
                    source_name=source_file.original_name,
                    output_path=output_path,
                    expected_quality=pipeline_result.quality_expectation(),
                )
            except Exception as exc:
                logger.exception("Database import failed for Excel Final job %s", job_id)
                db.rollback()
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"MySQL 入库失败: {exc}"),
                    error_code=ERROR_CODE_DB_IMPORT_FAILED,
                )
                return

            _add_step(
                db,
                job_id,
                attempt,
                STEP_IMPORT_PARTS_DB,
                worker_name,
                "succeeded",
                input_json={"output_path": str(output_path)},
                output_json=database_stats,
                started_at=import_started,
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=75,
                event=make_event(
                    type_="progress",
                    progress=75,
                    step_name=STEP_IMPORT_PARTS_DB,
                    status=JOB_RUNNING,
                    message=(
                        f"MySQL 入库完成: {batch.part_count} 个零件, "
                        f"{batch.component_count} 个构件"
                    ),
                    stats=database_stats,
                ),
            )
            if job is None:
                return

            persist_started = datetime.now(UTC)
            excel_bytes = output_path.read_bytes()
            excel_file = save_bytes_as_file(
                db,
                bucket=settings.minio_bucket_reports,
                storage_key=f"jobs/{job.id}/{uuid4().hex}{_EXCEL_EXT}",
                original_name=f"{output_basename}_处理后{_EXCEL_EXT}",
                file_ext=_EXCEL_EXT,
                content_type=_EXCEL_CONTENT_TYPE,
                payload=excel_bytes,
                uploaded_by=job.created_by,
            )
            result_payload = {
                "source": "excel_final",
                "job_id": job.id,
                "task_type": TASK_EXCEL_FINAL,
                "file_id": file_id,
                "format": source_format,
                "source_name": source_file.original_name,
                **database_stats,
                "excel_file_id": excel_file.id,
            }
            analysis = AnalysisResult(
                job_id=job.id,
                drawing_id=job.drawing_id,
                result_type=TASK_EXCEL_FINAL,
                result_json=result_payload,
                confidence=Decimal("1.0000"),
                result_file_id=excel_file.id,
                algorithm_version=_ALGORITHM_VERSION,
                tool_version="excel_final",
                status="succeeded",
            )
            db.add(analysis)
            db.flush()
            _add_step(
                db,
                job_id,
                attempt,
                STEP_PERSIST_EXCEL_FINAL,
                worker_name,
                "succeeded",
                input_json={"excel_size": len(excel_bytes)},
                output_json={
                    "excel_file_id": excel_file.id,
                    "analysis_result_id": analysis.id,
                    **database_stats,
                },
                started_at=persist_started,
            )
            complete_job_attempt(
                db,
                job_id,
                attempt=attempt,
                event=make_event(
                    type_="done",
                    status="succeeded",
                    progress=100,
                    step_name=STEP_PERSIST_EXCEL_FINAL,
                    message=_completion_message(batch),
                    excel_file_id=excel_file.id,
                    excel_name=f"{output_basename}_处理后{_EXCEL_EXT}",
                    part_count=batch.part_count,
                    component_count=batch.component_count,
                    **database_stats,
                ),
            )
    except Exception as exc:
        db.rollback()
        if "attempt" in locals():
            _mark_job_failed(
                db,
                job_id,
                attempt,
                exc,
                error_code=ERROR_CODE_PIPELINE_FAILED,
            )
        logger.exception("Excel Final processing failed for job %s", job_id)
    finally:
        db.close()


__all__ = ["run_excel_final_processing"]
