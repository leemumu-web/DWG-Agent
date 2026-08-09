"""Excel Stage3 编排 — 异孔折判断对接全流程。

该模块负责任务编排、输入验证、Stage 调用和产物持久化。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.modules.excel_processing.stage_adapter import (
    ExcelStage3ProcessResult,
    get_excel_stage3_root,
    run_excel_stage3_pipeline,
)
from app.platform.config.constants import (
    PIPELINE_EXCEL_STAGE3,
    STEP_PERSIST_EXCEL_STAGE3,
    STEP_RUN_EXCEL_STAGE3,
    STEP_VALIDATE_EXCEL_STAGE3_INPUTS,
    TASK_EXCEL_STAGE3,
)
from app.platform.config.settings import settings

logger = logging.getLogger(__name__)


def run_excel_stage3_processing(
    job_id: int,
    *,
    worker_name: str = "celery_excel_stage3",
    expected_attempt: int = 1,
) -> dict:
    """执行 Excel 第三阶段处理的完整流程。

    此函数由 Celery 任务调用，负责编排从输入验证到产物持久化的全流程。

    Args:
        job_id: 作业 ID。
        worker_name: 执行该任务的 Celery worker 名称。
        expected_attempt: 预期的作业尝试次数。

    Returns:
        包含 job_id、状态和统计信息的汇总字典。
    """
    from app.modules.jobs.interface import (
        JobNotFoundError,
        claim_queued_job,
        complete_job_attempt,
        mark_job_failed,
        update_job_progress,
    )

    job_record = None
    try:
        # 1. 认领作业
        job_record = claim_queued_job(
            job_id,
            pipeline=PIPELINE_EXCEL_STAGE3,
            task=TASK_EXCEL_STAGE3,
            worker_name=worker_name,
            expected_attempt=expected_attempt,
        )
        update_job_progress(job_id, 5, STEP_VALIDATE_EXCEL_STAGE3_INPUTS)

        # 2. 验证输入
        stage2_excel_path, dxf_dir = _resolve_stage3_inputs(job_record)
        update_job_progress(job_id, 15, STEP_RUN_EXCEL_STAGE3)

        # 3. 准备输出目录
        work_root = settings.excel_stage3_work_root
        work_dir = work_root / str(job_id)
        output_dir = work_dir / "output"
        work_dir.mkdir(parents=True, exist_ok=True)

        # 4. 运行 Stage3
        result: ExcelStage3ProcessResult = run_excel_stage3_pipeline(
            stage2_excel_path=stage2_excel_path,
            dxf_dir=dxf_dir,
            output_dir=output_dir,
        )

        # 5. 持久化产物
        update_job_progress(job_id, 85, STEP_PERSIST_EXCEL_STAGE3)
        _persist_stage3_outputs(job_id, job_record, result, work_dir)

        # 6. 完成作业
        update_job_progress(job_id, 100, "complete")
        complete_job_attempt(
            job_id,
            pipeline=PIPELINE_EXCEL_STAGE3,
            attempt=expected_attempt,
            summary={
                "bh_box_count": result.bh_box_count,
                "matched_count": result.matched_count,
                "unmatched_count": result.unmatched_count,
                "classified_dxf_count": result.classified_dxf_count,
                "filled_count": result.filled_count,
                "manual_count": result.manual_count,
            },
        )

        return {
            "job_id": job_id,
            "status": "complete",
            "filled": result.filled_count,
            "manual": result.manual_count,
        }

    except JobNotFoundError:
        logger.warning("Stage3 job %d was claimed by another worker", job_id)
        return {"job_id": job_id, "status": "skipped"}

    except Exception:
        logger.exception("Stage3 job %d failed", job_id)
        if job_record is not None:
            mark_job_failed(
                job_id,
                pipeline=PIPELINE_EXCEL_STAGE3,
                attempt=expected_attempt,
                error_message="Excel Stage3 processing failed",
            )
        raise


def _resolve_stage3_inputs(job_record) -> tuple[Path, Path]:
    """从作业记录中解析 Stage3 输入路径。

    Returns:
        (stage2_excel_path, dxf_directory_path)
    """
    params = job_record.params or {}

    stage2_excel = params.get("stage2_excel_path")
    if not stage2_excel:
        raise ValueError("Missing required param: stage2_excel_path")

    dxf_dir = params.get("dxf_dir")
    if not dxf_dir:
        raise ValueError("Missing required param: dxf_dir")

    stage2_path = Path(stage2_excel)
    if not stage2_path.is_file():
        raise FileNotFoundError(f"Stage2 Excel not found: {stage2_excel}")

    dxf_path = Path(dxf_dir)
    if not dxf_path.is_dir():
        raise FileNotFoundError(f"DXF directory not found: {dxf_dir}")

    return stage2_path, dxf_path


def _persist_stage3_outputs(
    job_id: int,
    job_record,
    result: ExcelStage3ProcessResult,
    work_dir: Path,
) -> None:
    """持久化 Stage3 输出产物。

    将分类结果 Excel 和深化后 Excel 注册为工作流产物。
    """
    from app.modules.storage.interface import register_workflow_file
    from app.modules.storage.schemas import WorkflowArtifactCreate

    # 注册分类结果表
    classification_path = Path(result.classification_excel)
    if classification_path.is_file():
        register_workflow_file(
            job_id=job_id,
            workflow_run_id=job_record.workflow_run_id,
            artifact=WorkflowArtifactCreate(
                artifact_type="classification_excel",
                file_path=str(classification_path),
                display_name=f"分类结果_{job_id}.xlsx",
            ),
        )

    # 注册深化后 Excel
    deepened_path = Path(result.deepend_excel)
    if deepened_path.is_file():
        register_workflow_file(
            job_id=job_id,
            workflow_run_id=job_record.workflow_run_id,
            artifact=WorkflowArtifactCreate(
                artifact_type="stage3_excel",
                file_path=str(deepened_path),
                display_name=f"深化Stage2_{job_id}.xlsx",
            ),
        )

    logger.info(
        "Stage3 outputs persisted for job %d: classification=%s, deepened=%s",
        job_id,
        result.classification_excel,
        result.deepend_excel,
    )
