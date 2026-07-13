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
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings

# 如果配置了 ODA_HOME，注入环境变量供 dxf_converter.check_env 探测
if settings.oda_home:
    os.environ.setdefault("ODA_HOME", settings.oda_home)

from app.core.constants import (
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PIPELINE_DXF2DWG,
    STEP_DOWNLOAD_SOURCE_DXF,
    STEP_PERSIST_DWG,
    STEP_RUN_ODA_CONVERT_DXF,
    TASK_DXF_TO_DWG,
)
from app.db.session import SessionLocal
from app.models.file import StoredFile
from app.models.job import Job, JobStep
from app.models.result import AnalysisResult
from app.services.dxf_stats import _count_dxf_stats, dxf_entity_summary
from app.services.job_events import make_event
from app.services.job_service import (
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    fail_job_attempt,
)
from app.services.storage_service import get_storage_backend, sanitize_filename, save_bytes_as_file
from app.storage.base import StorageError, StorageObjectNotFound

logger = logging.getLogger(__name__)

ERROR_CODE_DWG_FAILED = "DWG_CONVERSION_FAILED"
ERROR_CODE_SOURCE_MISSING = "DXF_SOURCE_FILE_MISSING"
_DWG_CONTENT_TYPE = "application/acad"
_DWG_EXT = ".dwg"
_ALGO_VERSION = "oda-file-converter"

# DXF $ACADVER → ODA output DWG version string.
# The DXF header variable $ACADVER indicates which DWG version the DXF was
# saved from.  Matching this avoids unnecessary version upgrades on round-trips.
#
# When the source DXF was produced by our own DWG→DXF pipeline we prefer the
# AnalysisResult reverse-lookup (_resolve_source_dwg_version) as it directly
# captures the original DWG version without needing to read the file.
_DXF_ACADVER_MAP: dict[str, str] = {
    "AC1012": "ACAD13",
    "AC1014": "ACAD14",
    "AC1015": "ACAD2000",
    "AC1018": "ACAD2004",
    "AC1021": "ACAD2007",
    "AC1024": "ACAD2010",
    "AC1027": "ACAD2013",
    "AC1032": "ACAD2018",
}

# All known ODA output versions — used to validate resolved versions
# before passing them to the converter (prevents corrupted metadata
# from causing silent conversion failures).
_KNOWN_ODA_VERSIONS: frozenset[str] = frozenset(_DXF_ACADVER_MAP.values())


def _resolve_source_dwg_version(db: Session, source_file_id: int) -> str | None:
    """Look up the original DWG version that produced this DXF file.

    When a DXF was created by our DWG→DXF pipeline the AnalysisResult record
    stores the detected source DWG version in ``tool_version``.  Using that
    same version for the return trip avoids unnecessary format upgrades and
    can reduce round-trip size loss from ~40 % to <1 %.

    Returns None when the DXF was uploaded directly (no prior conversion).
    """
    from app.core.constants import TASK_DWG_TO_DXF

    result = db.scalars(
        select(AnalysisResult).where(
            AnalysisResult.result_file_id == source_file_id,
            AnalysisResult.result_type == TASK_DWG_TO_DXF,
        )
    ).first()
    if result and result.tool_version:
        logger.info(
            "Resolved source DWG version %s from AnalysisResult#%d for DXF file#%d",
            result.tool_version,
            result.id,
            source_file_id,
        )
        return result.tool_version
    return None


def _detect_dxf_output_version(source_path: Path) -> str:
    """Scan the DXF header section for $ACADVER to pick a matching ODA
    output DWG version.  Falls back to settings.dxf2dwg_converter_version
    when the header is unreadable or doesn't contain a recognised value.

    Prefer ``_resolve_source_dwg_version()`` for DXFs produced by our
    own DWG→DXF pipeline — it avoids an extra file read and is more
    reliable for edge cases.
    """
    try:
        with source_path.open("r", encoding="utf-8", errors="ignore") as fh:
            for _ in range(200):  # header section is always near the top
                line = fh.readline()
                if not line:
                    break
                # DXF format: group-code line, then value line.
                # $ACADVER is on its own line (after group code 9);
                # the version string follows on the line after group
                # code 1.  We must skip one line to reach the value.
                if line.strip() == "$ACADVER":
                    fh.readline()  # skip group code (1)
                    val_line = fh.readline().strip()
                    if val_line in _DXF_ACADVER_MAP:
                        return _DXF_ACADVER_MAP[val_line]
                    break
        return settings.dxf2dwg_converter_version
    except OSError:
        return settings.dxf2dwg_converter_version


def _resolve_dwg_output_version(
    db: Session,
    source_file_id: int,
    source_path: Path,
    *,
    job_id: int | None = None,
) -> str:
    """Use recorded source version when valid, otherwise inspect `$ACADVER`."""
    resolved = _resolve_source_dwg_version(db, source_file_id)
    if resolved and resolved in _KNOWN_ODA_VERSIONS:
        return resolved
    if resolved:
        logger.warning(
            "Ignoring unknown version %r from AnalysisResult for job %s — "
            "falling back to $ACADVER detection",
            resolved,
            job_id,
        )
    return _detect_dxf_output_version(source_path)


def _exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message:
            return message
    message = str(exc)
    return message or exc.__class__.__name__


def _mark_job_failed(
    db: Session,
    job_id: int,
    attempt: int,
    exc: Exception,
    error_code: str = ERROR_CODE_DWG_FAILED,
) -> None:
    """在 worker 当前事务内提交失败状态与待写步骤。"""
    try:
        fail_job_attempt(
            db,
            job_id,
            attempt=attempt,
            error_code=error_code,
            error_message=_exception_message(exc),
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to mark job %s as failed", job_id)


def _resolve_source_file_id(job: Job) -> int | None:
    """从 job.params_json 取源 DXF 的 file_id（前端 POST job 时 params.file_id 携带）。"""
    params = job.params_json or {}
    raw = params.get("file_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def _stage_source_dwg(db: Session, job: Job, source_file_id: int, work_dir: Path) -> Path | None:
    """把源 DXF 从 storage 取到本地 work_dir（ODA 需要本地文件路径）。

    local backend: storage.local_path() 直接返回路径，无需复制（ODA 读只读）。
    minio backend: storage.iter_file() 流式写到 work_dir/source.dxf。
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

    # minio backend：流式下载到 work_dir，沿用原扩展名
    dest = work_dir / f"source{stored.file_ext or '.dxf'}"
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


def persist_dwg_conversion_result(
    db: Session,
    *,
    job_id: int,
    attempt: int,
    source_file_id: int,
    source_path: Path,
    source_stats: dict,
    output_version: str,
    result,
    worker_name: str,
) -> bool:
    """Persist one successful DWG result only for its still-active attempt."""
    job = db.get(Job, job_id, populate_existing=True)
    if job is None or job.status != JOB_RUNNING or job.attempt != attempt:
        db.rollback()
        return False

    persist_started = datetime.now(UTC)
    dwg_path = result.target
    if not dwg_path.is_file():
        _mark_job_failed(
            db,
            job_id,
            attempt,
            AppError(f"DWG 产物未生成: {dwg_path}"),
            error_code=ERROR_CODE_DWG_FAILED,
        )
        return False

    dwg_bytes = dwg_path.read_bytes()
    source_file = db.get(StoredFile, source_file_id)
    source_base = source_file.original_name if source_file else Path(source_path).name
    source_base = sanitize_filename(source_base)
    source_stem = source_base.rsplit(".", 1)[0] if "." in source_base else source_base
    storage_key = f"jobs/{job.id}/{uuid4().hex}{_DWG_EXT}"
    dwg_file = save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_derived,
        storage_key=storage_key,
        original_name=f"{source_stem}{_DWG_EXT}",
        file_ext=_DWG_EXT,
        content_type=_DWG_CONTENT_TYPE,
        payload=dwg_bytes,
        uploaded_by=job.created_by,
        batch_name=source_file.batch_name if source_file else None,
    )
    result_payload = {
        "source": "dxf2dwg_open_source",
        "job_id": job.id,
        "task_type": TASK_DXF_TO_DWG,
        "source_file_id": source_file_id,
        "dwg_file_id": dwg_file.id,
        "convert_result": result.to_dict(),
        "source_dxf_stats": source_stats,
    }
    analysis = AnalysisResult(
        job_id=job.id,
        drawing_id=job.drawing_id,
        result_type=TASK_DXF_TO_DWG,
        result_json=result_payload,
        confidence=Decimal("1.0000"),
        result_file_id=dwg_file.id,
        algorithm_version=_ALGO_VERSION,
        tool_version=output_version,
        status="succeeded",
    )
    db.add(analysis)
    db.flush()
    _add_step(
        db,
        job_id,
        attempt,
        STEP_PERSIST_DWG,
        worker_name,
        "succeeded",
        input_json={"dwg_size": len(dwg_bytes)},
        output_json={
            "dwg_file_id": dwg_file.id,
            "analysis_result_id": analysis.id,
            "source_entity_counts": source_stats.get("entity_counts", {}),
            "source_total_entities": source_stats.get("total_entities", 0),
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
            step_name=STEP_PERSIST_DWG,
            message="DXF→DWG 转换完成",
        ),
    )
    return completed_job is not None


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
                logger.warning("DXF2DWG job %s progress commit lost (concurrent claim?), leaving for reconcile", job_id)
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


class AppError(Exception):
    """dxf2dwg_service 内部业务错误（消息友好，不带 traceback 泄露）。"""
