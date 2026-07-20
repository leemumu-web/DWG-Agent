"""DXF→Excel 材料表提取编排服务。

链路: 按 batch_name 查询源 DXF 文件 → 下载到临时目录
      → dxf2excel.pipeline.process_all() 批量提取材料表
      → save_bytes_as_file 持久化 Excel → AnalysisResult 登记 → SSE 推送。

设计要点:
- 仿 dxf_service.run_dxf_conversion 的状态机结构。
- 批次模型: N 个 DXF → 1 个 Job → 1 个 Excel（区别于逐文件管线）。
- 每步写 job_steps + publish_job_event。
- 复用 save_bytes_as_file 写 Excel（bucket=dwg-reports）。
- 复用 AnalysisResult.result_file_id 关联 Excel 文件。
"""

from __future__ import annotations

import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.modules.cad_processing.dxf_to_excel.contracts import (
    ERROR_CODE_DXF2EXCEL_FAILED,
    ERROR_CODE_DXF2EXCEL_UNAVAILABLE,
    ERROR_CODE_EMPTY_BATCH,
)
from app.modules.cad_processing.dxf_to_excel.persistence import (
    persist_excel_extraction_result,
)
from app.modules.cad_processing.dxf_to_excel.staging import (
    batch_workbook_stem,
)
from app.modules.cad_processing.dxf_to_excel.staging import (
    resolve_batch_name as _resolve_batch_name,
)
from app.modules.cad_processing.dxf_to_excel.staging import (
    stage_dxf_batch as _stage_dxf_batch,
)
from app.modules.cad_processing.execution import (
    CadProcessingError as AppError,
)
from app.modules.cad_processing.execution import (
    add_job_step as _add_step,
)
from app.modules.cad_processing.execution import (
    exception_message as _exception_message,
)
from app.modules.cad_processing.execution import (
    mark_job_failed,
)
from app.modules.jobs.interface import (
    claim_queued_job,
    commit_job_progress,
    make_event,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    PIPELINE_DXF2EXCEL,
    STEP_DOWNLOAD_DXF_BATCH,
    STEP_RUN_DXF2EXCEL,
)
from app.platform.database.session import SessionLocal

logger = logging.getLogger(__name__)


def _mark_job_failed(
    db: Session,
    job_id: int,
    attempt: int,
    exc: Exception,
    error_code: str = ERROR_CODE_DXF2EXCEL_FAILED,
) -> None:
    mark_job_failed(
        db,
        job_id,
        attempt,
        exc,
        error_code=error_code,
        logger=logger,
    )


def run_dxf2excel_extraction(
    job_id: int,
    worker_name: str = "celery_dxf2excel",
    expected_attempt: int = 1,
) -> None:
    """Celery dxf2excel 队列任务体：batch DXF → Excel 全链路。

    失败不抛（除导入错误外），通过 job.status/error_code 体现。
    """
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_DXF2EXCEL,
            progress=10,
            message="开始提取 DXF 批次",
        )
        if job is None:
            logger.info("DXF2Excel job %s was not claimable", job_id)
            return
        attempt = job.attempt

        batch_name = _resolve_batch_name(job)
        if batch_name is None:
            _mark_job_failed(
                db,
                job_id,
                attempt,
                AppError("DXF2Excel job 缺少 params.batch_name"),
                error_code=ERROR_CODE_EMPTY_BATCH,
            )
            return

        with tempfile.TemporaryDirectory(prefix=f"dxf2excel_job_{job_id}_") as work_dir_str:
            work_dir = Path(work_dir_str)

            # ---- 2. 下载 batch 内所有 DXF ----
            download_started = datetime.now(UTC)
            dxf_paths, download_stats = _stage_dxf_batch(db, batch_name, work_dir)

            if not dxf_paths and download_stats["dxf_count"] == 0:
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_DOWNLOAD_DXF_BATCH,
                    worker_name,
                    "failed",
                    input_json={"batch_name": batch_name},
                    error_message=f"批次 '{batch_name}' 中没有 .dxf 文件",
                    started_at=download_started,
                )
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"批次 '{batch_name}' 中没有 .dxf 文件"),
                    error_code=ERROR_CODE_EMPTY_BATCH,
                )
                return

            _add_step(
                db,
                job_id,
                attempt,
                STEP_DOWNLOAD_DXF_BATCH,
                worker_name,
                "succeeded" if not download_stats["errors"] else "succeeded_with_warnings",
                input_json={"batch_name": batch_name},
                output_json=download_stats,
                error_message=(
                    f"{len(download_stats['errors'])} files failed to download"
                    if download_stats["errors"]
                    else None
                ),
                started_at=download_started,
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=30,
                event=make_event(
                    type_="progress",
                    progress=30,
                    step_name=STEP_DOWNLOAD_DXF_BATCH,
                    status=JOB_RUNNING,
                    message=f"已下载 {download_stats['downloaded']}/{download_stats['dxf_count']} 个 DXF",
                    stats=download_stats,
                ),
            )
            if job is None:
                return

            if not dxf_paths:
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"批次 '{batch_name}' 所有 DXF 下载失败"),
                    error_code=ERROR_CODE_EMPTY_BATCH,
                )
                return

            # ---- 3. 逐文件运行 dxf2excel pipeline（实时进度） ----
            pipeline_started = datetime.now(UTC)
            output_path = work_dir / f"{batch_workbook_stem(batch_name)}.xlsx"

            try:
                from dxf2excel.excel_writer import write_excel
                from dxf2excel.models import TableResult
                from dxf2excel.pipeline import process_file as _pf
            except ImportError as exc:
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_RUN_DXF2EXCEL,
                    worker_name,
                    "failed",
                    input_json={"batch_name": batch_name, "dxf_count": len(dxf_paths)},
                    error_message=f"dxf2excel 包不可用: {exc}",
                    started_at=pipeline_started,
                )
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"dxf2excel 包不可用: {exc}"),
                    error_code=ERROR_CODE_DXF2EXCEL_UNAVAILABLE,
                )
                return

            pipeline_stats: dict = {
                "dxf_count": len(dxf_paths),
                "tables_found": 0,
                "data_rows": 0,
                "warnings_count": 0,
                "processing_errors": [],
            }

            all_tables: list = []
            all_warnings: list = []
            total_files = len(dxf_paths)
            success_count = 0

            # Commit progress every N files so frontend polling sees intermediate
            # values. Large batches commit less often to balance DB load.
            _COMMIT_EVERY_N = 3 if total_files <= 30 else 5

            for i, fp in enumerate(dxf_paths):
                try:
                    tables, warnings = _pf(fp)
                    all_tables.extend(tables)
                    all_warnings.extend(warnings)
                    if tables:
                        success_count += 1
                except Exception:
                    logger.warning("DXF2Excel: failed to process %s", fp.name, exc_info=True)
                    pipeline_stats["processing_errors"].append(str(fp.name))

                # Per-file progress: 30 → 70 mapped across all files
                file_progress = 30 + int(40 * (i + 1) / total_files)
                progress_event = make_event(
                    type_="progress",
                    progress=file_progress,
                    step_name=STEP_RUN_DXF2EXCEL,
                    status=JOB_RUNNING,
                    message=f"提取中: {i + 1}/{total_files} — {fp.name}",
                    file_index=i + 1,
                    total_files=total_files,
                    current_file=fp.name,
                )
                # Commit to MySQL every N files so polling sees intermediate values
                if (i + 1) % _COMMIT_EVERY_N == 0 or i + 1 == total_files:
                    job = commit_job_progress(
                        db,
                        job_id,
                        attempt=attempt,
                        progress=file_progress,
                        event=progress_event,
                    )
                    if job is None:
                        return

            # Count stats from collected results
            pipeline_stats["tables_found"] = len(all_tables)
            pipeline_stats["data_rows"] = sum(
                len(t.data_rows) for t in all_tables if isinstance(t, TableResult)
            )
            pipeline_stats["warnings_count"] = len(all_warnings)

            # Write combined Excel from collected results
            try:
                write_excel(output_path, all_tables, all_warnings)
            except Exception as exc:
                logger.exception("dxf2excel write_excel failed for job %s", job_id)
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_RUN_DXF2EXCEL,
                    worker_name,
                    "failed",
                    input_json={"batch_name": batch_name, "dxf_count": len(dxf_paths)},
                    output_json=pipeline_stats,
                    error_message=_exception_message(exc),
                    started_at=pipeline_started,
                )
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"Excel 写入失败: {exc}"),
                    error_code=ERROR_CODE_DXF2EXCEL_FAILED,
                )
                return

            _add_step(
                db,
                job_id,
                attempt,
                STEP_RUN_DXF2EXCEL,
                worker_name,
                "succeeded",
                input_json={"batch_name": batch_name, "dxf_count": len(dxf_paths)},
                output_json=pipeline_stats,
                started_at=pipeline_started,
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=70,
                event=make_event(
                    type_="progress",
                    progress=70,
                    step_name=STEP_RUN_DXF2EXCEL,
                    status=JOB_RUNNING,
                    message=f"材料表提取完成: {success_count}/{total_files} 个文件, "
                    f"{pipeline_stats['tables_found']} 张表, "
                    f"{pipeline_stats['data_rows']} 行数据",
                    stats=pipeline_stats,
                ),
            )
            if job is None:
                return

            if not persist_excel_extraction_result(
                db,
                job_id=job_id,
                attempt=attempt,
                batch_name=batch_name,
                output_path=output_path,
                pipeline_stats=pipeline_stats,
                worker_name=worker_name,
            ):
                return

    except Exception as exc:
        db.rollback()
        if "attempt" in locals():
            _mark_job_failed(
                db,
                job_id,
                attempt,
                exc,
                error_code=ERROR_CODE_DXF2EXCEL_FAILED,
            )
        logger.exception("DXF2Excel extraction failed for job %s", job_id)
    finally:
        db.close()
