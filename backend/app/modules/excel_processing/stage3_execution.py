"""Excel Stage3 编排 — 异孔折判断对接全流程。

该模块负责任务编排、输入验证、Stage 调用和产物持久化。
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.modules.excel_processing.stage_adapter import (
    ExcelStage3ProcessResult,
    get_excel_stage3_root,
    run_excel_stage3_pipeline,
)
from app.modules.files.interface import StoredFile, get_storage_backend
from app.platform.config.constants import (
    PIPELINE_EXCEL_STAGE3,
    STEP_PERSIST_EXCEL_STAGE3,
    STEP_RUN_EXCEL_STAGE3,
    STEP_VALIDATE_EXCEL_STAGE3_INPUTS,
    TASK_EXCEL_STAGE3,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.platform.storage.base import StorageObjectNotFound

logger = logging.getLogger(__name__)

_PARAM_FIELDS = frozenset({
    "workflow_id",
    "project_id",
    "stage2_excel_file_id",
    "processed_dxf_file_ids",
    "stage2_job_id",
    "stage2_job_attempt",
})


class Stage3WorkerError(RuntimeError):
    """A stable worker failure safe to expose to production operators."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _positive_int(params: dict, field: str) -> int:
    value = params.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise Stage3WorkerError(
            "EXCEL_STAGE3_JOB_PARAMS_INVALID",
            "Excel 第三阶段任务的冻结参数不完整，请重新运行。",
        )
    return value


def _download_stored_file(stored: StoredFile, destination: Path) -> Path:
    """Export a StoredFile from object storage to a local path."""
    storage = get_storage_backend()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    try:
        local = storage.local_path(stored.bucket, stored.storage_key)
        if local is not None:
            if not local.is_file():
                raise StorageObjectNotFound(f"{stored.bucket}/{stored.storage_key}")
            with local.open("rb") as source, temporary.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
        else:
            with temporary.open("wb") as output:
                for chunk in storage.iter_file(stored.bucket, stored.storage_key):
                    output.write(chunk)
                    digest.update(chunk)
        temporary.replace(destination)
        return destination
    except StorageObjectNotFound as exc:
        raise Stage3WorkerError(
            "EXCEL_STAGE3_INPUT_OBJECT_MISSING",
            f"输入文件 {stored.original_name} 已从存储器中丢失。",
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def run_excel_stage3_processing(
    job_id: int,
    *,
    worker_name: str = "celery_excel_stage3",
    expected_attempt: int = 1,
) -> dict:
    """执行 Excel 第三阶段处理的完整流程。

    此函数由 Celery 任务调用，负责编排从输入验证到产物持久化的全流程。
    """
    from app.modules.jobs.interface import (
        claim_queued_job,
        commit_job_progress,
        complete_job_attempt,
        fail_job_attempt,
        make_event,
    )

    db = SessionLocal()
    job_record = None
    try:
        # 1. 认领作业（原子性跨进程幂等边界）
        job_record = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_EXCEL_STAGE3,
            progress=5,
            message="开始核验 Excel 第三阶段输入",
        )
        if job_record is None:
            logger.warning("Stage3 job %d was claimed by another worker", job_id)
            return {"job_id": job_id, "status": "skipped"}
        attempt = job_record.attempt

        # 2. 准备输出目录
        work_root = settings.excel_stage3_work_root
        work_dir = work_root / str(job_id)
        output_dir = work_dir / "output"
        work_dir.mkdir(parents=True, exist_ok=True)

        # 3. 验证输入并导出文件
        stage2_excel_path, dxf_dir = _resolve_stage3_inputs(job_record, work_dir)

        commit_job_progress(
            db,
            job_id,
            attempt=attempt,
            progress=15,
            event=make_event(
                type_="progress",
                status="running",
                progress=15,
                message="输入核验完成，开始运行异孔折判断",
                step=STEP_RUN_EXCEL_STAGE3,
            ),
        )

        # 4. 运行 Stage3
        result: ExcelStage3ProcessResult = run_excel_stage3_pipeline(
            stage2_excel_path=stage2_excel_path,
            dxf_dir=dxf_dir,
            output_dir=output_dir,
        )

        # 5. 持久化产物
        commit_job_progress(
            db,
            job_id,
            attempt=attempt,
            progress=85,
            event=make_event(
                type_="progress",
                status="running",
                progress=85,
                message="正在保存分类结果",
                step=STEP_PERSIST_EXCEL_STAGE3,
            ),
        )
        _persist_stage3_outputs(db, job_record, attempt, result)

        # 6. 完成作业
        summary = {
            "bh_box_count": result.bh_box_count,
            "matched_count": result.matched_count,
            "unmatched_count": result.unmatched_count,
            "classified_dxf_count": result.classified_dxf_count,
            "filled_count": result.filled_count,
            "manual_count": result.manual_count,
        }
        complete_job_attempt(
            db,
            job_id,
            attempt=attempt,
            event=make_event(
                type_="done",
                status="succeeded",
                progress=100,
                message="异孔折判断完成",
                **summary,
            ),
        )

        return {
            "job_id": job_id,
            "status": "complete",
            "filled": result.filled_count,
            "manual": result.manual_count,
        }

    except Exception:
        logger.exception("Stage3 job %d failed", job_id)
        if job_record is not None:
            try:
                fail_job_attempt(
                    db,
                    job_id,
                    attempt=job_record.attempt,
                    error_code="EXCEL_STAGE3_PROCESSING_FAILED",
                    error_message="Excel 第三阶段处理失败",
                )
            except Exception:
                logger.exception("Failed to mark Stage3 job %d as failed", job_id)
        raise
    finally:
        db.close()


def _resolve_stage3_inputs(job_record, work_dir: Path) -> tuple[Path, Path]:
    """从作业记录中解析 Stage3 输入，将存储文件导出到本地工作目录。

    Returns:
        (stage2_excel_path, dxf_directory_path)
    """
    params = job_record.params_json if isinstance(job_record.params_json, dict) else {}
    if frozenset(params) != _PARAM_FIELDS:
        raise Stage3WorkerError(
            "EXCEL_STAGE3_JOB_PARAMS_INVALID",
            "Excel 第三阶段任务的冻结参数不完整，请重新运行。",
        )

    stage2_file_id = _positive_int(params, "stage2_excel_file_id")
    dxf_file_ids = params.get("processed_dxf_file_ids")
    if not isinstance(dxf_file_ids, list):
        raise Stage3WorkerError(
            "EXCEL_STAGE3_JOB_PARAMS_INVALID",
            "Excel 第三阶段任务的 processed_dxf_file_ids 无效。",
        )

    db = SessionLocal()
    try:
        # Export Stage 2 Excel
        stage2_stored = db.get(StoredFile, stage2_file_id)
        if stage2_stored is None or stage2_stored.status == "deleted":
            raise Stage3WorkerError(
                "EXCEL_STAGE3_INPUT_OBJECT_MISSING",
                "第二阶段 Excel 文件已不可用。",
            )
        stage2_excel_path = _download_stored_file(
            stage2_stored,
            work_dir / "input" / stage2_stored.original_name,
        )

        # Export processed DXF files
        dxf_dir = work_dir / "input" / "dxf"
        dxf_dir.mkdir(parents=True, exist_ok=True)
        exported_count = 0
        for dxf_id in dxf_file_ids:
            if not isinstance(dxf_id, int):
                continue
            dxf_stored = db.get(StoredFile, dxf_id)
            if dxf_stored is None or dxf_stored.status == "deleted":
                logger.warning(
                    "Stage3 skipping unavailable DXF file_id=%d", dxf_id
                )
                continue
            _download_stored_file(
                dxf_stored,
                dxf_dir / dxf_stored.original_name,
            )
            exported_count += 1

        logger.info(
            "Stage3 inputs resolved: stage2=%s, dxf_count=%d",
            stage2_excel_path,
            exported_count,
        )

        if exported_count == 0:
            logger.warning(
                "Stage3 job %d: no DXF files available for classification",
                job_record.id,
            )

    finally:
        db.close()

    return stage2_excel_path, dxf_dir


def _persist_stage3_outputs(
    db,
    job_record,
    attempt: int,
    result: ExcelStage3ProcessResult,
) -> None:
    """持久化 Stage3 输出产物到对象存储并创建 AnalysisResult 记录。

    将分类结果 Excel 和深化后 Excel 上传至 MinIO 并注册为工作流可通过
    artifact 机制引用的产物。
    """
    from uuid import uuid4

    from app.modules.files.interface import (
        complete_transfer_in_transaction,
        prepare_generated_file_transfer,
        save_bytes_as_file,
    )
    from app.modules.jobs.interface import AnalysisResult

    _EXCEL_CONTENT_TYPE = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    def _store_workbook(path_str: str, artifact_type: str, original_name: str) -> None:
        path = Path(path_str)
        if not path.is_file():
            logger.warning(
                "Stage3 output %s not found at %s", artifact_type, path_str
            )
            return
        payload = path.read_bytes()
        storage_key = (
            f"jobs/{job_record.id}/attempt-{attempt}/"
            f"{artifact_type}-{uuid4().hex}.xlsx"
        )
        transfer_uid = prepare_generated_file_transfer(
            db,
            actor_user_id=job_record.created_by,
            request_id=f"job:{job_record.id}:attempt:{attempt}:{artifact_type}",
            batch_ref=None,
            bucket=settings.minio_bucket_reports,
            storage_key=storage_key,
            original_name=original_name,
            expected_bytes=len(payload),
        )
        stored = save_bytes_as_file(
            db,
            bucket=settings.minio_bucket_reports,
            storage_key=storage_key,
            original_name=original_name,
            file_ext=".xlsx",
            content_type=_EXCEL_CONTENT_TYPE,
            payload=payload,
            uploaded_by=job_record.created_by,
            transfer_uid=transfer_uid,
        )
        complete_transfer_in_transaction(
            db,
            transfer_uid,
            file_id=stored.id,
            bucket=stored.bucket,
            storage_key=stored.storage_key,
            original_name=stored.original_name,
            transferred_bytes=stored.size_bytes,
        )
        analysis = AnalysisResult(
            job_id=job_record.id,
            result_type=TASK_EXCEL_STAGE3,
            result_json={
                "source": "excel_stage3",
                "workflow_artifact_type": artifact_type,
                "job_attempt": attempt,
            },
            confidence=1.0,
            result_file_id=stored.id,
            algorithm_version="excel-stage3-v1",
            tool_version="excel_stage3",
            status="succeeded",
        )
        db.add(analysis)
        logger.info(
            "Stage3 persisted %s: file_id=%d, result_id will be available after commit",
            artifact_type,
            stored.id,
        )

    _store_workbook(
        result.classification_excel,
        "classification_excel",
        f"异孔折分类结果_{job_record.id}.xlsx",
    )
    _store_workbook(
        result.deepened_excel,
        "stage3_excel",
        f"深化Stage2_{job_record.id}.xlsx",
    )
