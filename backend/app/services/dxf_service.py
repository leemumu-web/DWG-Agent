"""DWG→DXF 转换编排服务（spec §14, Stage 3 DXF 管线）。

链路:  storage 源 DWG → dwg_converter.convert_file(ODA subprocess) → DXF bytes
       → save_bytes_as_file(dwg-derived) → AnalysisResult 登记 → job_steps + 进度推送。

设计要点:
- 仿 job_service.run_local_stub_job 的状态机结构，但调用真实 ODA 引擎。
- 转换失败（ConvertResult.success=False）→ job.status=failed, error_code=DXF_CONVERSION_FAILED，不抛。
- 环境错误（OdaConvertError：找不到 ODA/xvfb）→ 抛异常 → except 分支标记 failed。
- 每步写 job_steps（step_name/worker_name/status/input_json/output_json）+ publish_job_event。
- 复用 save_bytes_as_file 写派生 DXF，自动建 StoredFile 行（bucket=dwg-derived）。
- 复用 AnalysisResult.result_file_id 关联 DXF 文件，前端走 /results/{id}/download-url 下载。
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings

# 如果配置了 ODA_HOME，注入环境变量供 dwg_converter.check_env 探测
if settings.oda_home:
    os.environ.setdefault("ODA_HOME", settings.oda_home)

# 框架集成适配层 — dwg_converter.framework 提供统一错误码映射
from dwg_converter.framework import ERROR_CODES as _EC

from app.core.constants import (
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PIPELINE_DXF,
    STEP_DOWNLOAD_SOURCE,
    STEP_PERSIST_DXF,
    STEP_RUN_ODA_CONVERT,
    TASK_DWG_TO_DXF,
)
from app.db.session import SessionLocal
from app.models.file import StoredFile
from app.models.job import Job, JobStep
from app.models.result import AnalysisResult
from app.services.dxf_stats import _count_dxf_stats, dxf_entity_summary
from app.services.job_events import make_event, publish_job_event
from app.services.storage_service import get_storage_backend, sanitize_filename, save_bytes_as_file
from app.storage.base import StorageError, StorageObjectNotFound

logger = logging.getLogger(__name__)

ERROR_CODE_DXF_FAILED = _EC["DXF_CONVERSION_FAILED"]
ERROR_CODE_SOURCE_MISSING = _EC["DXF_SOURCE_MISSING"]
_DXF_CONTENT_TYPE = "application/dxf"
_DXF_EXT = ".dxf"
_ALGO_VERSION = "oda-file-converter"

# DWG header magic → ODA File Converter version string.
# Keeping the same output version as the source avoids unnecessary binary
# restructuring (AC1015 → ACAD2000, not ACAD2018), reducing round-trip loss.
# ODA File Converter supports ACAD13/ACAD14 for R13/R14 — mapping these to
# ACAD2018 would gratuitously upgrade the file 8 generations.
_DWG_VERSION_MAP: dict[bytes, str] = {
    b"AC1012": "ACAD13",    # R13
    b"AC1014": "ACAD14",    # R14
    b"AC1015": "ACAD2000",
    b"AC1018": "ACAD2004",
    b"AC1021": "ACAD2007",
    b"AC1024": "ACAD2010",
    b"AC1027": "ACAD2013",
    b"AC1032": "ACAD2018",
}

# All known ODA output versions — used to validate resolved versions.
_KNOWN_ODA_VERSIONS: frozenset[str] = frozenset(_DWG_VERSION_MAP.values())


def _detect_dwg_output_version(source_path: Path) -> str:
    """Read the DWG header to pick a matching ODA output version.

    Falls back to settings.oda_converter_version when the header is
    unreadable or unknown.
    """
    try:
        with source_path.open("rb") as fh:
            header = fh.read(6)
        return _DWG_VERSION_MAP.get(header, settings.oda_converter_version)
    except OSError:
        return settings.oda_converter_version


def _exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message:
            return message
    message = str(exc)
    return message or exc.__class__.__name__


def _mark_job_failed(job_id: int, exc: Exception, error_code: str = ERROR_CODE_DXF_FAILED) -> None:
    """独立 session 标记任务失败（仿 job_service._mark_job_failed）。"""
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
        logger.exception("Failed to mark job %s as failed", job_id)
    finally:
        db.close()


def _resolve_source_file_id(job: Job) -> int | None:
    """从 job.params_json 取源 DWG 的 file_id（前端 POST job 时 params.file_id 携带）。"""
    params = job.params_json or {}
    raw = params.get("file_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _stage_source_dwg(db: Session, job: Job, source_file_id: int, work_dir: Path) -> Path | None:
    """把源 DWG 从 storage 取到本地 work_dir（ODA 需要本地文件路径）。

    local backend: storage.local_path() 直接返回路径，无需复制（ODA 读只读）。
    minio backend: storage.iter_file() 流式写到 work_dir/source.dwg。
    返回本地 Path 或 None（源文件不存在）。
    """
    from app.models.file import StoredFile

    stored = db.get(StoredFile, source_file_id)
    if not stored or stored.status == "deleted":
        return None

    storage = get_storage_backend()
    local_path = storage.local_path(stored.bucket, stored.storage_key)
    if local_path is not None:
        if not local_path.exists() or not local_path.is_file():
            raise StorageObjectNotFound(f"{stored.bucket}/{stored.storage_key}")
        return local_path  # local backend：ODA 直接读

    # minio backend：流式下载到 work_dir，沿用原扩展名（ODA 按内容识别，但保留 .dwg 更稳妥）
    dest = work_dir / f"source{stored.file_ext or '.dwg'}"
    try:
        with dest.open("wb") as out:
            for chunk in storage.iter_file(stored.bucket, stored.storage_key):
                out.write(chunk)
    except StorageObjectNotFound:
        return None
    return dest


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


def run_dxf_conversion(job_id: int, worker_name: str = "celery_dxf") -> None:
    """Celery dxf 队列任务体：DWG → DXF 全链路（spec §14.4 流程）。

    失败不抛（除环境错误 OdaConvertError 外），通过 job.status/error_code 体现。
    """
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            logger.warning("DXF job %s not found", job_id)
            return
        if job.status != JOB_QUEUED:
            logger.info("DXF job %s skipped (status=%s)", job_id, job.status)
            return

        source_file_id = _resolve_source_file_id(job)
        if source_file_id is None:
            _mark_job_failed(
                job_id,
                AppError("DXF job 缺少 params.file_id"),
                error_code=ERROR_CODE_SOURCE_MISSING,
            )
            return

        # ---- 1. queued → running，写 download_source_dwg 步 ----
        started_at = datetime.now(UTC)
        job.status = JOB_RUNNING
        job.progress = 10
        job.started_at = started_at
        job.pipeline = PIPELINE_DXF
        publish_job_event(
            db,
            job_id,
            make_event(type_="status", status=JOB_RUNNING, progress=10, message="开始转换"),
        )
        db.commit()

        with tempfile.TemporaryDirectory(prefix=f"dxf_job_{job_id}_") as work_dir_str:
            work_dir = Path(work_dir_str)
            try:
                source_path = _stage_source_dwg(db, job, source_file_id, work_dir)
            except StorageObjectNotFound:
                _mark_job_failed(
                    job_id,
                    AppError(f"源文件不存在 file_id={source_file_id}"),
                    error_code=ERROR_CODE_SOURCE_MISSING,
                )
                _add_step(
                    db, job_id, STEP_DOWNLOAD_SOURCE, worker_name, "failed",
                    input_json={"file_id": source_file_id},
                    error_message="源文件对象不存在",
                    started_at=started_at,
                )
                db.commit()
                return
            except StorageError as exc:
                raise AppError(f"读取源文件失败: {exc}") from exc

            if source_path is None:
                _mark_job_failed(
                    job_id,
                    AppError(f"源文件不存在 file_id={source_file_id}"),
                    error_code=ERROR_CODE_SOURCE_MISSING,
                )
                _add_step(
                    db, job_id, STEP_DOWNLOAD_SOURCE, worker_name, "failed",
                    input_json={"file_id": source_file_id},
                    error_message="源文件记录不存在或已删除",
                    started_at=started_at,
                )
                db.commit()
                return

            _add_step(
                db, job_id, STEP_DOWNLOAD_SOURCE, worker_name, "succeeded",
                input_json={"file_id": source_file_id},
                output_json={"source_path": str(source_path)},
                started_at=started_at,
            )
            job.progress = 30
            publish_job_event(
                db,
                job_id,
                make_event(type_="progress", progress=30, step_name=STEP_DOWNLOAD_SOURCE, message="源文件已就绪"),
            )
            db.commit()

            # ---- 2. 调 ODA 转换 ----
            convert_started = datetime.now(UTC)
            out_dir = work_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            try:
                from dwg_converter import convert_file  # 延迟 import，避免测试环境强依赖
            except ImportError as exc:
                raise AppError(f"dwg_converter 包不可用: {exc}") from exc

            try:
                output_version = _detect_dwg_output_version(source_path)
                result = convert_file(
                    source=source_path,
                    target_dir=out_dir,
                    version=output_version,
                    audit=settings.oda_converter_audit,
                    timeout=settings.oda_converter_timeout,
                    retries=settings.oda_converter_retries,
                )
            except Exception as exc:
                # OdaConvertError（环境错误）或其他异常 → 失败
                _add_step(
                    db, job_id, STEP_RUN_ODA_CONVERT, worker_name, "failed",
                    input_json={
                        "version": output_version,
                        "audit": settings.oda_converter_audit,
                        "timeout": settings.oda_converter_timeout,
                    },
                    error_message=_exception_message(exc),
                    started_at=convert_started,
                )
                db.commit()
                raise AppError(f"ODA 转换异常: {exc}") from exc

            _add_step(
                db, job_id, STEP_RUN_ODA_CONVERT, worker_name,
                "succeeded" if result.success else "failed",
                input_json={
                    "version": output_version,
                    "audit": settings.oda_converter_audit,
                    "timeout": settings.oda_converter_timeout,
                },
                output_json=result.to_dict(),
                error_message=result.error if not result.success else None,
                started_at=convert_started,
            )
            job.progress = 70
            publish_job_event(
                db,
                job_id,
                make_event(
                    type_="progress",
                    progress=70,
                    step_name=STEP_RUN_ODA_CONVERT,
                    status=JOB_RUNNING,
                    message="ODA 转换完成" if result.success else f"ODA 转换失败: {result.error}",
                ),
            )
            db.commit()

            if not result.success:
                _mark_job_failed(
                    job_id,
                    AppError(result.error or "ODA 转换失败"),
                    error_code=ERROR_CODE_DXF_FAILED,
                )
                return

            # ---- 3. 持久化 DXF 产物 ----
            persist_started = datetime.now(UTC)
            dxf_path = result.target
            if not dxf_path.is_file():
                _mark_job_failed(
                    job_id,
                    AppError(f"DXF 产物未生成: {dxf_path}"),
                    error_code=ERROR_CODE_DXF_FAILED,
                )
                return

            dxf_bytes = dxf_path.read_bytes()
            dxf_stats = _count_dxf_stats(dxf_path)
            logger.info(
                "DXF conversion stats for job %s: %s", job_id, dxf_entity_summary(dxf_stats),
            )

            # Use the original DWG filename — the work-dir path may be a temp name
            source_file = db.get(StoredFile, source_file_id)
            source_base = (source_file.original_name if source_file else Path(source_path).name)
            source_base = sanitize_filename(source_base)
            source_stem = source_base.rsplit(".", 1)[0] if "." in source_base else source_base
            storage_key = f"jobs/{job.id}/{uuid4().hex}{_DXF_EXT}"
            dxf_file = save_bytes_as_file(
                db,
                bucket=settings.minio_bucket_dxf_derived,
                storage_key=storage_key,
                original_name=f"{source_stem}{_DXF_EXT}",
                file_ext=_DXF_EXT,
                content_type=_DXF_CONTENT_TYPE,
                payload=dxf_bytes,
                uploaded_by=job.created_by,
                batch_name=source_file.batch_name if source_file else None,
            )

            # 行锁重读 job，防并发取消
            job = db.scalars(select(Job).where(Job.id == job_id).with_for_update()).one_or_none()
            if not job or job.status != JOB_RUNNING:
                db.rollback()
                return

            result_payload = {
                "source": "dxf_open_source",
                "job_id": job.id,
                "task_type": TASK_DWG_TO_DXF,
                "source_file_id": source_file_id,
                "dxf_file_id": dxf_file.id,
                "convert_result": result.to_dict(),
                "dxf_stats": dxf_stats,
            }
            analysis = AnalysisResult(
                job_id=job.id,
                drawing_id=job.drawing_id,
                result_type=TASK_DWG_TO_DXF,
                result_json=result_payload,
                confidence=Decimal("1.0000"),
                result_file_id=dxf_file.id,
                algorithm_version=_ALGO_VERSION,
                tool_version=output_version,
                status="succeeded",
            )
            db.add(analysis)
            db.flush()  # 让 analysis.id 可用
            _add_step(
                db, job_id, STEP_PERSIST_DXF, worker_name, "succeeded",
                input_json={"dxf_size": len(dxf_bytes)},
                output_json={
                    "dxf_file_id": dxf_file.id,
                    "analysis_result_id": analysis.id,
                    "entity_counts": dxf_stats.get("entity_counts", {}),
                    "total_entities": dxf_stats.get("total_entities", 0),
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
                    step_name=STEP_PERSIST_DXF,
                    message="DXF 转换完成",
                ),
            )
            db.commit()
    except Exception as exc:
        db.rollback()
        _mark_job_failed(job_id, exc, error_code=ERROR_CODE_DXF_FAILED)
        logger.exception("DXF conversion failed for job %s", job_id)
    finally:
        db.close()


class AppError(Exception):
    """dxf_service 内部业务错误（消息友好，不带 traceback 泄露）。"""
