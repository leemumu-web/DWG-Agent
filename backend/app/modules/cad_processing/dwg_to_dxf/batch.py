"""DWG-to-DXF batch execution with one authoritative Job per source."""

from __future__ import annotations

import logging
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.modules.cad_processing.batching import _convert_oda_group
from app.modules.cad_processing.dwg_to_dxf.contracts import (
    ERROR_CODE_DXF_FAILED,
    ERROR_CODE_SOURCE_MISSING,
)
from app.modules.cad_processing.dwg_to_dxf.execution import (
    AppError,
    _add_step,
    _mark_job_failed,
    _resolve_source_file_id,
    _stage_source_dwg,
)
from app.modules.cad_processing.dwg_to_dxf.persistence import persist_dxf_conversion_result
from app.modules.cad_processing.dwg_to_dxf.versions import detect_dwg_output_version
from app.modules.jobs.interface import claim_queued_job, commit_job_progress, make_event
from app.platform.config.constants import (
    JOB_RUNNING,
    PIPELINE_DXF,
    STEP_DOWNLOAD_SOURCE,
    STEP_RUN_ODA_CONVERT,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _DwgBatchItem:
    job_id: int
    attempt: int
    source_file_id: int
    source_path: Path
    staged_path: Path
    output_version: str


def _fail_dwg_item(
    db,
    *,
    job_id: int,
    attempt: int,
    worker_name: str,
    message: str,
    step_name: str,
    error_code: str,
    started_at: datetime,
) -> None:
    _add_step(
        db,
        job_id,
        attempt,
        step_name,
        worker_name,
        "failed",
        error_message=message,
        started_at=started_at,
    )
    _mark_job_failed(
        db,
        job_id,
        attempt,
        AppError(message),
        error_code=error_code,
    )


def run_dwg_to_dxf_batch(
    jobs: list[tuple[int, int]],
    *,
    worker_name: str = "celery_dxf_batch",
) -> dict[str, int]:
    """Convert queued DWG jobs by ODA output version in directory-sized calls."""
    summary = {"total": len(jobs), "succeeded": 0, "failed": 0, "skipped": 0}
    db = SessionLocal()
    try:
        with tempfile.TemporaryDirectory(prefix="dwg2dxf_batch_") as root_str:
            root = Path(root_str)
            grouped: dict[str, list[_DwgBatchItem]] = defaultdict(list)

            for job_id, expected_attempt in jobs:
                job = claim_queued_job(
                    db,
                    job_id,
                    expected_attempt=expected_attempt,
                    pipeline=PIPELINE_DXF,
                    progress=10,
                    message="批量转换任务已领取",
                    event_data={"batch_size": len(jobs)},
                )
                if job is None:
                    summary["skipped"] += 1
                    continue
                attempt = job.attempt
                source_file_id = _resolve_source_file_id(job)
                stage_started = job.started_at or datetime.now(UTC)
                if source_file_id is None:
                    _fail_dwg_item(
                        db,
                        job_id=job_id,
                        attempt=attempt,
                        worker_name=worker_name,
                        message="DWG 批量任务缺少 params.file_id",
                        step_name=STEP_DOWNLOAD_SOURCE,
                        error_code=ERROR_CODE_SOURCE_MISSING,
                        started_at=stage_started,
                    )
                    summary["failed"] += 1
                    continue

                job_work_dir = root / "downloads" / f"job-{job_id}"
                job_work_dir.mkdir(parents=True, exist_ok=True)
                try:
                    source_path = _stage_source_dwg(db, job, source_file_id, job_work_dir)
                except Exception as exc:
                    _fail_dwg_item(
                        db,
                        job_id=job_id,
                        attempt=attempt,
                        worker_name=worker_name,
                        message=f"读取 DWG 源文件失败: {exc}",
                        step_name=STEP_DOWNLOAD_SOURCE,
                        error_code=ERROR_CODE_SOURCE_MISSING,
                        started_at=stage_started,
                    )
                    summary["failed"] += 1
                    continue
                if source_path is None:
                    _fail_dwg_item(
                        db,
                        job_id=job_id,
                        attempt=attempt,
                        worker_name=worker_name,
                        message="DWG 源文件记录不存在或已删除",
                        step_name=STEP_DOWNLOAD_SOURCE,
                        error_code=ERROR_CODE_SOURCE_MISSING,
                        started_at=stage_started,
                    )
                    summary["failed"] += 1
                    continue

                output_version = detect_dwg_output_version(source_path)
                version_dir = root / "groups" / output_version / "input"
                version_dir.mkdir(parents=True, exist_ok=True)
                staged_path = version_dir / f"job-{job_id}.dwg"
                shutil.copy2(source_path, staged_path)
                _add_step(
                    db,
                    job_id,
                    attempt,
                    STEP_DOWNLOAD_SOURCE,
                    worker_name,
                    "succeeded",
                    input_json={"file_id": source_file_id},
                    output_json={"batch_group": output_version},
                    started_at=stage_started,
                )
                current = commit_job_progress(
                    db,
                    job_id,
                    attempt=attempt,
                    progress=30,
                    event=make_event(
                        type_="progress",
                        status=JOB_RUNNING,
                        progress=30,
                        step_name=STEP_DOWNLOAD_SOURCE,
                        message="源文件已加入批量转换组",
                        batch_group=output_version,
                    ),
                )
                if current is None:
                    summary["skipped"] += 1
                    continue
                grouped[output_version].append(
                    _DwgBatchItem(
                        job_id=job_id,
                        attempt=attempt,
                        source_file_id=source_file_id,
                        source_path=source_path,
                        staged_path=staged_path,
                        output_version=output_version,
                    )
                )

            from dwg_converter import convert_directory

            for output_version, items in grouped.items():
                input_dir = items[0].staged_path.parent
                output_dir = input_dir.parent / "output"
                convert_started = datetime.now(UTC)
                try:
                    results = _convert_oda_group(
                        staged_paths=[item.staged_path for item in items],
                        output_root=output_dir,
                        convert_directory=convert_directory,
                        converter_kwargs={
                            "version": output_version,
                            "audit": settings.oda_converter_audit,
                            "timeout": settings.oda_converter_timeout,
                            "retries": settings.oda_converter_retries,
                        },
                    )
                    result_by_name = {result.source.name: result for result in results}
                except Exception as exc:
                    logger.exception("DWG batch ODA call failed for version=%s", output_version)
                    result_by_name = {}
                    group_error = f"ODA 批量转换异常: {exc}"
                else:
                    group_error = "ODA 批量转换未返回对应文件结果"

                for item in items:
                    result = result_by_name.get(item.staged_path.name)
                    if result is None:
                        _fail_dwg_item(
                            db,
                            job_id=item.job_id,
                            attempt=item.attempt,
                            worker_name=worker_name,
                            message=group_error,
                            step_name=STEP_RUN_ODA_CONVERT,
                            error_code=ERROR_CODE_DXF_FAILED,
                            started_at=convert_started,
                        )
                        summary["failed"] += 1
                        continue

                    _add_step(
                        db,
                        item.job_id,
                        item.attempt,
                        STEP_RUN_ODA_CONVERT,
                        worker_name,
                        "succeeded" if result.success else "failed",
                        input_json={
                            "version": output_version,
                            "audit": settings.oda_converter_audit,
                            "batch_size": len(items),
                        },
                        output_json=result.to_dict(),
                        error_message=result.error if not result.success else None,
                        started_at=convert_started,
                    )
                    current = commit_job_progress(
                        db,
                        item.job_id,
                        attempt=item.attempt,
                        progress=70,
                        event=make_event(
                            type_="progress",
                            status=JOB_RUNNING,
                            progress=70,
                            step_name=STEP_RUN_ODA_CONVERT,
                            message=(
                                "ODA 批量转换完成"
                                if result.success
                                else f"ODA 批量转换失败: {result.error}"
                            ),
                        ),
                    )
                    if current is None:
                        summary["skipped"] += 1
                        continue
                    if not result.success:
                        _mark_job_failed(
                            db,
                            item.job_id,
                            item.attempt,
                            AppError(result.error or "ODA 批量转换失败"),
                            error_code=ERROR_CODE_DXF_FAILED,
                        )
                        summary["failed"] += 1
                        continue
                    try:
                        persisted = persist_dxf_conversion_result(
                            db,
                            job_id=item.job_id,
                            attempt=item.attempt,
                            source_file_id=item.source_file_id,
                            source_path=item.source_path,
                            output_version=item.output_version,
                            result=result,
                            worker_name=worker_name,
                        )
                    except Exception as exc:
                        db.rollback()
                        _mark_job_failed(
                            db,
                            item.job_id,
                            item.attempt,
                            exc,
                            error_code=ERROR_CODE_DXF_FAILED,
                        )
                        logger.exception("Failed to persist batched DXF job %s", item.job_id)
                        summary["failed"] += 1
                        continue
                    if persisted:
                        summary["succeeded"] += 1
                    else:
                        summary["skipped"] += 1
    finally:
        db.close()
    return summary


__all__ = ["run_dwg_to_dxf_batch"]
