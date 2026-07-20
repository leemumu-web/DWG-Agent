"""DXF→DWG 转换编排服务（spec §14, Stage 3 DXF 管线）。

链路:  storage 源 DXF → dxf_converter.convert_file(ODA subprocess) → DXF bytes
       → save_bytes_as_file(dwg-derived) → AnalysisResult 登记 → job_steps + 进度推送。

设计要点:
- 仿 job_service.run_local_stub_job 的状态机结构，但调用真实 ODA 引擎。
- 转换失败（ConvertResult.success=False）→ job.status=failed, error_code=DWG_CONVERSION_FAILED，不抛。
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
from pathlib import Path

from sqlalchemy.orm import Session

from app.platform.config.settings import settings

# 如果配置了 ODA_HOME，注入环境变量供 dxf_converter.check_env 探测
if settings.oda_home:
    os.environ.setdefault("ODA_HOME", settings.oda_home)

from app.modules.cad_processing.dxf_to_dwg.contracts import (
    ERROR_CODE_DWG_FAILED,
    ERROR_CODE_SOURCE_MISSING,
)
from app.modules.cad_processing.dxf_to_dwg.persistence import persist_dwg_conversion_result
from app.modules.cad_processing.dxf_to_dwg.versions import (
    resolve_dwg_output_version as _resolve_dwg_output_version,
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
    stage_source_file,
)
from app.modules.cad_processing.execution import (
    resolve_source_file_id as _resolve_source_file_id,
)
from app.modules.cad_processing.statistics import _count_dxf_stats, dxf_entity_summary
from app.modules.jobs.interface import (
    Job,
    claim_queued_job,
    commit_job_progress,
    make_event,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    PIPELINE_DXF2DWG,
    STEP_DOWNLOAD_SOURCE_DXF,
    STEP_RUN_ODA_CONVERT_DXF,
)
from app.platform.database.session import SessionLocal
from app.platform.storage.base import StorageError, StorageObjectNotFound

logger = logging.getLogger(__name__)


def _mark_job_failed(
    db: Session,
    job_id: int,
    attempt: int,
    exc: Exception,
    error_code: str = ERROR_CODE_DWG_FAILED,
) -> None:
    mark_job_failed(
        db,
        job_id,
        attempt,
        exc,
        error_code=error_code,
        logger=logger,
    )


def _stage_source_dwg(db: Session, job: Job, source_file_id: int, work_dir: Path) -> Path | None:
    return stage_source_file(
        db,
        source_file_id,
        work_dir,
        fallback_extension=".dxf",
    )


def run_dxf_to_dwg_conversion(
    job_id: int,
    worker_name: str = "celery_dxf2dwg",
    expected_attempt: int = 1,
) -> None:
    """Celery dxf2dwg 队列任务体：DXF → DWG 全链路。

    失败不抛（除环境错误 OdaConvertError 外），通过 job.status/error_code 体现。
    """
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_DXF2DWG,
            progress=10,
            message="开始转换",
        )
        if job is None:
            logger.info("DXF2DWG job %s was not claimable", job_id)
            return
        attempt = job.attempt

        source_file_id = _resolve_source_file_id(job)
        if source_file_id is None:
            _mark_job_failed(
                db,
                job_id,
                attempt,
                AppError("DXF job 缺少 params.file_id"),
                error_code=ERROR_CODE_SOURCE_MISSING,
            )
            return

        # ---- 1. queued → running，写 download_source_dwg 步 ----
        started_at = job.started_at or datetime.now(UTC)

        with tempfile.TemporaryDirectory(prefix=f"dxf_job_{job_id}_") as work_dir_str:
            work_dir = Path(work_dir_str)
            try:
                source_path = _stage_source_dwg(db, job, source_file_id, work_dir)
            except StorageObjectNotFound:
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_DOWNLOAD_SOURCE_DXF,
                    worker_name,
                    "failed",
                    input_json={"file_id": source_file_id},
                    error_message="源文件对象不存在",
                    started_at=started_at,
                )
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"源文件不存在 file_id={source_file_id}"),
                    error_code=ERROR_CODE_SOURCE_MISSING,
                )
                return
            except StorageError as exc:
                raise AppError(f"读取源文件失败: {exc}") from exc

            if source_path is None:
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_DOWNLOAD_SOURCE_DXF,
                    worker_name,
                    "failed",
                    input_json={"file_id": source_file_id},
                    error_message="源文件记录不存在或已删除",
                    started_at=started_at,
                )
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(f"源文件不存在 file_id={source_file_id}"),
                    error_code=ERROR_CODE_SOURCE_MISSING,
                )
                return

            _add_step(
                db,
                job_id,
                attempt,
                STEP_DOWNLOAD_SOURCE_DXF,
                worker_name,
                "succeeded",
                input_json={"file_id": source_file_id},
                output_json={"source_path": str(source_path)},
                started_at=started_at,
            )
            # Count entities in the source DXF for fidelity tracking.
            source_stats = _count_dxf_stats(source_path)
            logger.info(
                "DXF source stats for job %s: %s",
                job_id,
                dxf_entity_summary(source_stats),
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=30,
                event=make_event(
                    type_="progress",
                    progress=30,
                    step_name=STEP_DOWNLOAD_SOURCE_DXF,
                    message="源文件已就绪",
                ),
            )
            if job is None:
                logger.warning(
                    "DXF2DWG job %s progress commit lost (concurrent claim?), leaving for reconcile",
                    job_id,
                )
                return

            # ---- 2. 调 ODA 转换 ----
            convert_started = datetime.now(UTC)
            out_dir = work_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            try:
                from dxf_converter import convert_file  # 延迟 import，避免测试环境强依赖
            except ImportError as exc:
                raise AppError(f"dxf_converter 包不可用: {exc}") from exc

            try:
                # Prefer reverse-lookup through AnalysisResult for round-trip
                # fidelity.  Fall back to $ACADVER scanning for external DXFs.
                # Guard against corrupted metadata: if the resolved version is
                # not a known ODA version string, discard it and fall through.
                output_version = _resolve_dwg_output_version(
                    db,
                    source_file_id,
                    source_path,
                    job_id=job_id,
                )
                result = convert_file(
                    source=source_path,
                    target_dir=out_dir,
                    version=output_version,
                    audit=settings.dxf2dwg_converter_audit,
                    timeout=settings.dxf2dwg_converter_timeout,
                    retries=settings.dxf2dwg_converter_retries,
                )
            except Exception as exc:
                # OdaConvertError（环境错误）或其他异常 → 失败
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_RUN_ODA_CONVERT_DXF,
                    worker_name,
                    "failed",
                    input_json={
                        "version": output_version,
                        "audit": settings.dxf2dwg_converter_audit,
                        "timeout": settings.dxf2dwg_converter_timeout,
                    },
                    error_message=_exception_message(exc),
                    started_at=convert_started,
                )
                job = commit_job_progress(
                    db,
                    job_id,
                    attempt=attempt,
                    progress=job.progress,
                    event=make_event(
                        type_="progress",
                        status=JOB_RUNNING,
                        progress=job.progress,
                        step_name=STEP_RUN_ODA_CONVERT_DXF,
                        message="ODA 转换异常",
                    ),
                )
                if job is None:
                    return
                raise AppError(f"ODA 转换异常: {exc}") from exc

            _add_step(
                db,
                job_id,
                attempt,
                STEP_RUN_ODA_CONVERT_DXF,
                worker_name,
                "succeeded" if result.success else "failed",
                input_json={
                    "version": output_version,
                    "audit": settings.dxf2dwg_converter_audit,
                    "timeout": settings.dxf2dwg_converter_timeout,
                },
                output_json=result.to_dict(),
                error_message=result.error if not result.success else None,
                started_at=convert_started,
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=70,
                event=make_event(
                    type_="progress",
                    progress=70,
                    step_name=STEP_RUN_ODA_CONVERT_DXF,
                    status=JOB_RUNNING,
                    message="ODA 转换完成" if result.success else f"ODA 转换失败: {result.error}",
                ),
            )
            if job is None:
                return

            if not result.success:
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(result.error or "ODA 转换失败"),
                    error_code=ERROR_CODE_DWG_FAILED,
                )
                return

            # ---- 3. 持久化 DWG 产物 ----
            if not persist_dwg_conversion_result(
                db,
                job_id=job_id,
                attempt=attempt,
                source_file_id=source_file_id,
                source_path=source_path,
                source_stats=source_stats,
                output_version=output_version,
                result=result,
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
                error_code=ERROR_CODE_DWG_FAILED,
            )
        logger.exception("DXF conversion failed for job %s", job_id)
    finally:
        db.close()
