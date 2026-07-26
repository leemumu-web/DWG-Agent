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
from pathlib import Path

from sqlalchemy.orm import Session

from app.platform.config.settings import settings

# 如果配置了 ODA_HOME，注入环境变量供 dwg_converter.check_env 探测
if settings.oda_home:
    os.environ.setdefault("ODA_HOME", settings.oda_home)

from app.modules.cad_processing.dwg_to_dxf.contracts import (
    ERROR_CODE_DXF_FAILED,
    ERROR_CODE_SOURCE_MISSING,
)
from app.modules.cad_processing.dwg_to_dxf.persistence import persist_dxf_conversion_result
from app.modules.cad_processing.dwg_to_dxf.progress import (
    CLAIMED,
    ODA_CONVERTING,
    ODA_RESULT_READY,
    phase_data,
    phase_event,
    safe_convert_result_metadata,
    safe_failure_message,
)
from app.modules.cad_processing.dwg_to_dxf.versions import (
    detect_dwg_output_version as _detect_dwg_output_version,
)
from app.modules.cad_processing.execution import (
    CadProcessingError as AppError,
)
from app.modules.cad_processing.execution import (
    add_job_step as _add_step,
)
from app.modules.cad_processing.execution import (
    mark_job_failed,
    stage_source_file,
)
from app.modules.cad_processing.execution import (
    resolve_source_file_id as _resolve_source_file_id,
)
from app.modules.jobs.interface import (
    Job,
    claim_queued_job,
    commit_job_progress,
    make_event,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    PIPELINE_DXF,
    STEP_DOWNLOAD_SOURCE,
    STEP_RUN_ODA_CONVERT,
)
from app.platform.database.session import SessionLocal
from app.platform.storage.base import StorageError, StorageObjectNotFound

logger = logging.getLogger(__name__)


def _mark_job_failed(
    db: Session,
    job_id: int,
    attempt: int,
    exc: Exception,
    error_code: str = ERROR_CODE_DXF_FAILED,
) -> None:
    logger.error(
        "DWG-to-DXF job %s attempt %s failed internally: %s",
        job_id,
        attempt,
        exc,
        exc_info=not isinstance(exc, AppError),
    )
    mark_job_failed(
        db,
        job_id,
        attempt,
        AppError(safe_failure_message(error_code)),
        error_code=error_code,
        logger=logger,
    )


def _stage_source_dwg(db: Session, job: Job, source_file_id: int, work_dir: Path) -> Path | None:
    return stage_source_file(
        db,
        source_file_id,
        work_dir,
        fallback_extension=".dwg",
    )


def run_dxf_conversion(
    job_id: int,
    worker_name: str = "celery_dxf",
    expected_attempt: int = 1,
) -> None:
    """Celery dxf 队列任务体：DWG → DXF 全链路（spec §14.4 流程）。

    失败不抛（除环境错误 OdaConvertError 外），通过 job.status/error_code 体现。
    """
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_DXF,
            progress=CLAIMED.progress,
            message=CLAIMED.message,
            event_data=phase_data(CLAIMED),
        )
        if job is None:
            logger.info("DXF job %s was not claimable", job_id)
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
                    STEP_DOWNLOAD_SOURCE,
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
                    STEP_DOWNLOAD_SOURCE,
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
                STEP_DOWNLOAD_SOURCE,
                worker_name,
                "succeeded",
                input_json={"file_id": source_file_id},
                output_json={
                    "source_file_id": source_file_id,
                    "source_size_bytes": source_path.stat().st_size,
                },
                started_at=started_at,
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=ODA_CONVERTING.progress,
                event=phase_event(
                    ODA_CONVERTING,
                    step_name=STEP_DOWNLOAD_SOURCE,
                ),
            )
            if job is None:
                logger.warning(
                    "DWG2DXF job %s progress commit lost (concurrent claim?), leaving for reconcile",
                    job_id,
                )
                return

            # ---- 2. 调 ODA 转换 ----
            convert_started = datetime.now(UTC)
            out_dir = work_dir / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            try:
                from dwg_converter import convert_file  # 延迟 import，避免测试环境强依赖
            except ImportError as exc:
                raise AppError(f"dwg_converter 包不可用: {exc}") from exc

            try:
                output_version = "unknown"
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
                    db,
                    job_id,
                    attempt,
                    STEP_RUN_ODA_CONVERT,
                    worker_name,
                    "failed",
                    input_json={
                        "version": output_version,
                        "audit": settings.oda_converter_audit,
                        "timeout": settings.oda_converter_timeout,
                    },
                    error_message=safe_failure_message(ERROR_CODE_DXF_FAILED),
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
                        step_name=STEP_RUN_ODA_CONVERT,
                        message="ODA 转换异常",
                    ),
                )
                if job is None:
                    logger.warning(
                        "DWG2DXF job %s progress commit lost (concurrent claim?), leaving for reconcile",
                        job_id,
                    )
                    return
                raise AppError(f"ODA 转换异常: {exc}") from exc

            _add_step(
                db,
                job_id,
                attempt,
                STEP_RUN_ODA_CONVERT,
                worker_name,
                "succeeded" if result.success else "failed",
                input_json={
                    "version": output_version,
                    "audit": settings.oda_converter_audit,
                    "timeout": settings.oda_converter_timeout,
                },
                output_json=safe_convert_result_metadata(result),
                error_message=(
                    safe_failure_message(ERROR_CODE_DXF_FAILED)
                    if not result.success
                    else None
                ),
                started_at=convert_started,
            )
            job = commit_job_progress(
                db,
                job_id,
                attempt=attempt,
                progress=ODA_RESULT_READY.progress,
                event=phase_event(
                    ODA_RESULT_READY,
                    step_name=STEP_RUN_ODA_CONVERT,
                    message="ODA 转换完成" if result.success else f"ODA 转换失败: {result.error}",
                ),
            )
            if job is None:
                logger.warning(
                    "DWG2DXF job %s progress commit lost (concurrent claim?), leaving for reconcile",
                    job_id,
                )
                return

            if not result.success:
                _mark_job_failed(
                    db,
                    job_id,
                    attempt,
                    AppError(result.error or "ODA 转换失败"),
                    error_code=ERROR_CODE_DXF_FAILED,
                )
                return

            # ---- 3. 持久化 DXF 产物 ----
            if not persist_dxf_conversion_result(
                db,
                job_id=job_id,
                attempt=attempt,
                source_file_id=source_file_id,
                source_path=source_path,
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
                error_code=ERROR_CODE_DXF_FAILED,
            )
        logger.exception("DXF conversion failed for job %s", job_id)
    finally:
        db.close()
