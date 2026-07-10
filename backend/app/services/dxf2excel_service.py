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
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import (
    JOB_FAILED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PIPELINE_DXF2EXCEL,
    STEP_DOWNLOAD_DXF_BATCH,
    STEP_PERSIST_EXCEL,
    STEP_RUN_DXF2EXCEL,
    TASK_DXF_TO_EXCEL,
)
from app.db.session import SessionLocal
from app.models.file import StoredFile
from app.models.job import Job, JobStep
from app.models.result import AnalysisResult
from app.services.job_events import make_event, publish_job_event
from app.services.job_service import claim_queued_job
from app.services.storage_service import (
    get_storage_backend,
    sanitize_filename,
    save_bytes_as_file,
)
from app.storage.base import StorageObjectNotFound

logger = logging.getLogger(__name__)

ERROR_CODE_EMPTY_BATCH = "DXF2EXCEL_EMPTY_BATCH"
ERROR_CODE_DXF2EXCEL_FAILED = "DXF2EXCEL_PIPELINE_FAILED"
ERROR_CODE_DXF2EXCEL_NO_OUTPUT = "DXF2EXCEL_NO_OUTPUT"
ERROR_CODE_DXF2EXCEL_UNAVAILABLE = "DXF2EXCEL_UNAVAILABLE"
ERROR_CODE_STORAGE_FAILED = "DXF2EXCEL_STORAGE_FAILED"

_EXCEL_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_EXCEL_EXT = ".xlsx"
_ALGO_VERSION = "dxf2excel"


def _exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message:
            return message
    message = str(exc)
    return message or exc.__class__.__name__


def _mark_job_failed(
    job_id: int, exc: Exception, error_code: str = ERROR_CODE_DXF2EXCEL_FAILED
) -> None:
    """独立 session 标记任务失败（仿 dxf_service._mark_job_failed）。"""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job and job.status not in (JOB_SUCCEEDED, "cancelled"):
            job.status = JOB_FAILED
            job.error_code = error_code
            job.error_message = _exception_message(exc)
            job.finished_at = datetime.now(UTC)
            publish_job_event(
                db,
                job_id,
                make_event(
                    type_="error",
                    status=JOB_FAILED,
                    error_code=error_code,
                    message=job.error_message or error_code,
                ),
            )
            db.commit()
    except Exception:
        logger.exception("Failed to mark dxf2excel job %s as failed", job_id)
    finally:
        db.close()


def _add_step(
    db: Session,
    job_id: int,
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


def _resolve_batch_name(job: Job) -> str | None:
    """从 job.params_json 取 batch_name。"""
    params = job.params_json or {}
    raw = params.get("batch_name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _stage_dxf_batch(
    db: Session,
    batch_name: str,
    work_dir: Path,
) -> tuple[list[Path], dict]:
    """下载 batch 内所有 .dxf 文件到 work_dir。

    Returns (local_paths, stats_dict) where stats_dict has keys:
        dxf_count, downloaded, total_bytes, errors
    """
    dxf_files = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.batch_name == batch_name,
                StoredFile.file_ext == ".dxf",
                StoredFile.status != "deleted",
            )
        ).all()
    )

    stats: dict = {
        "dxf_count": len(dxf_files),
        "downloaded": 0,
        "total_bytes": 0,
        "errors": [],
    }

    if not dxf_files:
        return [], stats

    storage = get_storage_backend()
    local_paths: list[Path] = []

    for sfile in dxf_files:
        try:
            local = storage.local_path(sfile.bucket, sfile.storage_key)
            if local is not None:
                # local backend: 直接使用路径（零拷贝）
                if not local.exists() or not local.is_file():
                    raise StorageObjectNotFound(f"{sfile.bucket}/{sfile.storage_key}")
                # 复制到 work_dir 以统一路径管理（process_all 不改源文件）
                dest = work_dir / sanitize_filename(sfile.original_name)
                dest.write_bytes(local.read_bytes())
            else:
                # minio backend: 流式下载
                dest = work_dir / sanitize_filename(sfile.original_name)
                with dest.open("wb") as out:
                    for chunk in storage.iter_file(sfile.bucket, sfile.storage_key):
                        out.write(chunk)
            local_paths.append(dest)
            stats["downloaded"] += 1
            stats["total_bytes"] += dest.stat().st_size
        except Exception as exc:
            logger.warning(
                "Failed to stage DXF %s (file_id=%s): %s",
                sfile.original_name,
                sfile.id,
                exc,
            )
            stats["errors"].append(
                {"file_id": sfile.id, "original_name": sfile.original_name, "error": str(exc)}
            )

    return local_paths, stats


def run_dxf2excel_extraction(job_id: int, worker_name: str = "celery_dxf2excel") -> None:
    """Celery dxf2excel 队列任务体：batch DXF → Excel 全链路。

    失败不抛（除导入错误外），通过 job.status/error_code 体现。
    """
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            pipeline=PIPELINE_DXF2EXCEL,
            progress=10,
            message="开始提取 DXF 批次",
        )
        if job is None:
            logger.info("DXF2Excel job %s was not claimable", job_id)
            return

        batch_name = _resolve_batch_name(job)
        if batch_name is None:
            _mark_job_failed(
                job_id,
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
                _mark_job_failed(
                    job_id,
                    AppError(f"批次 '{batch_name}' 中没有 .dxf 文件"),
                    error_code=ERROR_CODE_EMPTY_BATCH,
                )
                _add_step(
                    db,
                    job_id,
                    STEP_DOWNLOAD_DXF_BATCH,
                    worker_name,
                    "failed",
                    input_json={"batch_name": batch_name},
                    error_message=f"批次 '{batch_name}' 中没有 .dxf 文件",
                    started_at=download_started,
                )
                db.commit()
                return

            _add_step(
                db,
                job_id,
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
            job.progress = 30
            publish_job_event(
                db,
                job_id,
                make_event(
                    type_="progress",
                    progress=30,
                    step_name=STEP_DOWNLOAD_DXF_BATCH,
                    status=JOB_RUNNING,
                    message=f"已下载 {download_stats['downloaded']}/{download_stats['dxf_count']} 个 DXF",
                    stats=download_stats,
                ),
            )
            db.commit()

            if not dxf_paths:
                _mark_job_failed(
                    job_id,
                    AppError(f"批次 '{batch_name}' 所有 DXF 下载失败"),
                    error_code=ERROR_CODE_EMPTY_BATCH,
                )
                return

            # ---- 3. 逐文件运行 dxf2excel pipeline（实时进度） ----
            pipeline_started = datetime.now(UTC)
            output_path = work_dir / f"{sanitize_filename(batch_name)}.xlsx"

            try:
                from dxf2excel.excel_writer import write_excel
                from dxf2excel.models import TableResult
                from dxf2excel.pipeline import process_file as _pf
            except ImportError as exc:
                _mark_job_failed(
                    job_id,
                    AppError(f"dxf2excel 包不可用: {exc}"),
                    error_code=ERROR_CODE_DXF2EXCEL_UNAVAILABLE,
                )
                _add_step(
                    db,
                    job_id,
                    STEP_RUN_DXF2EXCEL,
                    worker_name,
                    "failed",
                    input_json={"batch_name": batch_name, "dxf_count": len(dxf_paths)},
                    error_message=f"dxf2excel 包不可用: {exc}",
                    started_at=pipeline_started,
                )
                db.commit()
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
                job.progress = file_progress
                publish_job_event(
                    db,
                    job_id,
                    make_event(
                        type_="progress",
                        progress=file_progress,
                        step_name=STEP_RUN_DXF2EXCEL,
                        status=JOB_RUNNING,
                        message=f"提取中: {i + 1}/{total_files} — {fp.name}",
                        file_index=i + 1,
                        total_files=total_files,
                        current_file=fp.name,
                    ),
                )
                # Commit to MySQL every N files so polling sees intermediate values
                if (i + 1) % _COMMIT_EVERY_N == 0:
                    db.commit()
                else:
                    db.flush()

            # Release any tail batch before Excel generation, which can be slow.
            db.commit()

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
                _mark_job_failed(
                    job_id,
                    AppError(f"Excel 写入失败: {exc}"),
                    error_code=ERROR_CODE_DXF2EXCEL_FAILED,
                )
                _add_step(
                    db,
                    job_id,
                    STEP_RUN_DXF2EXCEL,
                    worker_name,
                    "failed",
                    input_json={"batch_name": batch_name, "dxf_count": len(dxf_paths)},
                    output_json=pipeline_stats,
                    error_message=_exception_message(exc),
                    started_at=pipeline_started,
                )
                db.commit()
                return

            _add_step(
                db,
                job_id,
                STEP_RUN_DXF2EXCEL,
                worker_name,
                "succeeded",
                input_json={"batch_name": batch_name, "dxf_count": len(dxf_paths)},
                output_json=pipeline_stats,
                started_at=pipeline_started,
            )
            job.progress = 70
            publish_job_event(
                db,
                job_id,
                make_event(
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
            db.commit()

            # ---- 4. 持久化 Excel ----
            persist_started = datetime.now(UTC)
            if not output_path.is_file():
                _mark_job_failed(
                    job_id,
                    AppError("Excel 输出文件未生成"),
                    error_code=ERROR_CODE_DXF2EXCEL_NO_OUTPUT,
                )
                return

            excel_bytes = output_path.read_bytes()
            storage_key = f"jobs/{job.id}/{uuid4().hex}{_EXCEL_EXT}"
            output_basename = sanitize_filename(batch_name)

            excel_file = save_bytes_as_file(
                db,
                bucket=settings.minio_bucket_reports,
                storage_key=storage_key,
                original_name=f"{output_basename}{_EXCEL_EXT}",
                file_ext=_EXCEL_EXT,
                content_type=_EXCEL_CONTENT_TYPE,
                payload=excel_bytes,
                uploaded_by=job.created_by,
            )

            # 行锁重读 job，防并发取消
            job = db.scalars(select(Job).where(Job.id == job_id).with_for_update()).one_or_none()
            if not job or job.status != JOB_RUNNING:
                db.rollback()
                return

            result_payload = {
                "source": "dxf2excel",
                "job_id": job.id,
                "task_type": TASK_DXF_TO_EXCEL,
                "batch_name": batch_name,
                **pipeline_stats,
                "excel_file_id": excel_file.id,
            }
            analysis = AnalysisResult(
                job_id=job.id,
                drawing_id=job.drawing_id,
                result_type=TASK_DXF_TO_EXCEL,
                result_json=result_payload,
                confidence=Decimal("1.0000"),
                result_file_id=excel_file.id,
                algorithm_version=_ALGO_VERSION,
                tool_version="dxf2excel",
                status="succeeded",
            )
            db.add(analysis)
            db.flush()  # 让 analysis.id 可用

            _add_step(
                db,
                job_id,
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
            job.status = JOB_SUCCEEDED
            job.progress = 100
            job.finished_at = datetime.now(UTC)
            publish_job_event(
                db,
                job_id,
                make_event(
                    type_="done",
                    status=JOB_SUCCEEDED,
                    progress=100,
                    step_name=STEP_PERSIST_EXCEL,
                    message=f"Excel 已生成: {pipeline_stats['tables_found']} 张表, "
                    f"{pipeline_stats['data_rows']} 行数据, "
                    f"{pipeline_stats['warnings_count']} 个警告",
                    excel_file_id=excel_file.id,
                    excel_name=f"{output_basename}{_EXCEL_EXT}",
                    tables_found=pipeline_stats["tables_found"],
                    data_rows=pipeline_stats["data_rows"],
                    warnings_count=pipeline_stats["warnings_count"],
                ),
            )
            db.commit()

    except Exception as exc:
        db.rollback()
        _mark_job_failed(job_id, exc, error_code=ERROR_CODE_DXF2EXCEL_FAILED)
        logger.exception("DXF2Excel extraction failed for job %s", job_id)
    finally:
        db.close()


class AppError(Exception):
    """dxf2excel_service 内部业务错误（消息友好，不带 traceback 泄露）。"""
