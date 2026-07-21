"""Attempt-aware workflow orchestration for Steel DXF classification."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.dxf_classification.adapter import (
    CLASSIFIER_VERSION,
    CLI_SCHEMA,
    REPORT_SCHEMA,
    ClassificationError,
    classifier_project_name,
)
from app.modules.dxf_classification.adapter import (
    invoke_classifier as _invoke_classifier,
)
from app.modules.dxf_classification.adapter import (
    preprocessed_name as _preprocessed_name,
)
from app.modules.dxf_classification.adapter import (
    safe_route as _safe_route,
)
from app.modules.dxf_classification.persistence import (
    classification_sources as _classification_sources,
)
from app.modules.dxf_classification.persistence import (
    finish_classification_run,
    get_or_create_classification_run,
    load_classification_run,
    record_classification_analysis,
    record_classification_item,
)
from app.modules.dxf_classification.persistence import (
    mark_classification_failed as _mark_failed,
)
from app.modules.dxf_classification.persistence import (
    persist_output as _persist_output,
)
from app.modules.files.interface import StoredFile
from app.modules.jobs.interface import (
    JobStep,
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    make_event,
)
from app.modules.projects.interface import Project
from app.modules.workflows.interface import (
    WorkflowInputBatch,
    WorkflowInputItem,
    WorkflowRun,
    attach_artifact,
    read_verified_input_object,
)
from app.platform.config.constants import (
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PIPELINE_STEEL_DXF_CLASSIFIER,
    STEP_PERSIST_CLASSIFICATION,
    STEP_RUN_STEEL_DXF_CLASSIFIER,
    STEP_STAGE_CLASSIFIER_INPUT,
)
from app.platform.database.session import SessionLocal

logger = logging.getLogger(__name__)


def _add_step(
    db: Session,
    job_id: int,
    attempt: int,
    name: str,
    worker_name: str,
    *,
    input_json: dict[str, Any] | None = None,
    output_json: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(UTC)
    db.add(
        JobStep(
            job_id=job_id,
            attempt=attempt,
            step_name=name,
            worker_name=worker_name,
            status="succeeded",
            input_json=input_json,
            output_json=output_json,
            started_at=now,
            finished_at=now,
        )
    )


def run_dxf_classification(
    job_id: int,
    *,
    worker_name: str = "celery_dxf_classification",
    expected_attempt: int = 1,
) -> None:
    db = SessionLocal()
    try:
        job = claim_queued_job(
            db,
            job_id,
            expected_attempt=expected_attempt,
            pipeline=PIPELINE_STEEL_DXF_CLASSIFIER,
            progress=5,
            message="DXF 分类任务已接收",
        )
        if job is None:
            return
        attempt = job.attempt
        workflow_id = int((job.params_json or {}).get("workflow_id") or 0)
        workflow = db.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.id == workflow_id)
            .options(
                selectinload(WorkflowRun.input_batch).selectinload(WorkflowInputBatch.items),
                selectinload(WorkflowRun.stages),
                selectinload(WorkflowRun.artifacts),
            )
        )
        if workflow is None or workflow.current_stage != "dxf_classification":
            raise ClassificationError("Job 未绑定当前 DXF 分类阶段。")
        project = db.get(Project, workflow.project_id)
        if project is None:
            raise ClassificationError("生产项目不存在。")
        sources = _classification_sources(db, workflow)
        project_name = classifier_project_name(project.code, workflow.id)
        batch = workflow.input_batch
        assert batch is not None and batch.manifest_sha256 is not None
        run = get_or_create_classification_run(
            db,
            job=job,
            workflow=workflow,
            attempt=attempt,
            project_name=project_name,
            manifest_sha256=batch.manifest_sha256,
            input_count=len(sources),
        )

        with tempfile.TemporaryDirectory(
            prefix=f"dxf-classification-{job.id}-{attempt}-"
        ) as raw_root:
            root = Path(raw_root)
            input_directory = root / f"{project_name}_dxf"
            input_directory.mkdir()
            by_output_name: dict[str, tuple[WorkflowInputItem, StoredFile, bytes]] = {}
            for item, stored in sources:
                name = Path(stored.original_name).name
                payload = read_verified_input_object(stored)
                output_name = _preprocessed_name(name)
                key = output_name.casefold()
                if key in by_output_name:
                    raise ClassificationError(f"分类预处理文件名冲突: {output_name}")
                (input_directory / name).write_bytes(payload)
                by_output_name[key] = (item, stored, payload)
            _add_step(
                db,
                job.id,
                attempt,
                STEP_STAGE_CLASSIFIER_INPUT,
                worker_name,
                output_json={"input_count": len(sources), "manifest_sha256": batch.manifest_sha256},
            )
            job = commit_job_progress(
                db,
                job.id,
                attempt=attempt,
                progress=25,
                event=make_event(
                    type_="progress", status=JOB_RUNNING, progress=25, message="分类输入已校验"
                ),
            )
            if job is None:
                return

            cli_payload = _invoke_classifier(input_directory)
            report_path = root / f"{project_name}_分类报告.json"
            manifest_path = root / f"{project_name}_分类清单.csv"
            if not report_path.is_file() or not manifest_path.is_file():
                raise ClassificationError("分类器未生成 JSON 报告或 CSV 清单。")
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ClassificationError("分类 JSON 报告不可读取。") from exc
            if report.get("schema") != REPORT_SCHEMA or cli_payload.get("schema") != CLI_SCHEMA:
                raise ClassificationError("分类器输出 schema 不符合 1.1 契约。")
            results = report.get("results")
            summary = report.get("summary")
            if (
                not isinstance(results, list)
                or not isinstance(summary, dict)
                or len(results) != len(sources)
            ):
                raise ClassificationError("分类报告逐图数量与冻结输入不一致。")
            _add_step(
                db,
                job.id,
                attempt,
                STEP_RUN_STEEL_DXF_CLASSIFIER,
                worker_name,
                input_json={"classifier_version": CLASSIFIER_VERSION},
                output_json={"status": cli_payload.get("status"), **summary},
            )
            job = commit_job_progress(
                db,
                job.id,
                attempt=attempt,
                progress=70,
                event=make_event(
                    type_="progress", status=JOB_RUNNING, progress=70, message="DXF 分类分流完成"
                ),
            )
            if job is None:
                return

            run = load_classification_run(
                db,
                job_id=job.id,
                attempt=attempt,
            )
            seen: set[str] = set()
            for result in results:
                if not isinstance(result, dict):
                    raise ClassificationError("分类报告逐图记录格式错误。")
                output_name = result.get("source_name")
                if not isinstance(output_name, str) or Path(output_name).name != output_name:
                    raise ClassificationError("分类报告包含非法文件名。")
                source_tuple = by_output_name.get(output_name.casefold())
                if source_tuple is None or output_name.casefold() in seen:
                    raise ClassificationError("分类报告无法与冻结 DXF 一一对应。")
                seen.add(output_name.casefold())
                input_item, source_file, source_payload = source_tuple
                route = _safe_route(project_name, result.get("output_directory"))
                output_path = root / route / output_name
                if not output_path.is_file():
                    raise ClassificationError(f"分类输出缺失: {route}/{output_name}")
                output_payload = output_path.read_bytes()
                if (
                    hashlib.sha256(output_payload).hexdigest()
                    != hashlib.sha256(source_payload).hexdigest()
                ):
                    raise ClassificationError(f"分类输出字节与来源不一致: {output_name}")
                output_file = _persist_output(
                    db,
                    job=job,
                    workflow_id=workflow.id,
                    attempt=attempt,
                    relative_path=f"{route}/{output_name}",
                    path=output_path,
                    batch_name=route,
                    content_type="application/dxf",
                )
                record_classification_item(
                    db,
                    run=run,
                    input_item=input_item,
                    source_file=source_file,
                    output_file=output_file,
                    output_name=output_name,
                    route=route,
                    result=result,
                )
                attach_artifact(
                    db,
                    workflow,
                    stage_code="dxf_classification",
                    artifact_type="classified_dxf",
                    file_id=output_file.id,
                    metadata={
                        "source_file_id": source_file.id,
                        "drawing_id": input_item.drawing_id,
                        "disposition": result.get("disposition"),
                        "part_type": result.get("part_type"),
                        "output_directory": route,
                        "classifier_version": CLASSIFIER_VERSION,
                    },
                )

            report_file = _persist_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=attempt,
                relative_path=report_path.name,
                path=report_path,
                batch_name=f"{project_name}_classification",
                content_type="application/json",
            )
            manifest_file = _persist_output(
                db,
                job=job,
                workflow_id=workflow.id,
                attempt=attempt,
                relative_path=manifest_path.name,
                path=manifest_path,
                batch_name=f"{project_name}_classification",
                content_type="text/csv",
            )
            analysis = record_classification_analysis(
                db,
                job=job,
                workflow_id=workflow.id,
                run=run,
                cli_payload=cli_payload,
                summary=summary,
                report_file=report_file,
                manifest_file=manifest_file,
            )
            attach_artifact(
                db,
                workflow,
                stage_code="dxf_classification",
                artifact_type="classification_report",
                file_id=report_file.id,
                result_id=analysis.id,
                metadata={"schema": REPORT_SCHEMA, "classifier_version": CLASSIFIER_VERSION},
            )
            attach_artifact(
                db,
                workflow,
                stage_code="dxf_classification",
                artifact_type="classification_manifest",
                file_id=manifest_file.id,
                metadata={"classifier_version": CLASSIFIER_VERSION},
            )
            finish_classification_run(
                run,
                cli_payload=cli_payload,
                summary=summary,
                report_file=report_file,
                manifest_file=manifest_file,
            )
            _add_step(
                db,
                job.id,
                attempt,
                STEP_PERSIST_CLASSIFICATION,
                worker_name,
                output_json={
                    "run_id": run.id,
                    "output_count": len(results),
                    "report_file_id": report_file.id,
                    "manifest_file_id": manifest_file.id,
                },
            )
            complete_job_attempt(
                db,
                job.id,
                attempt=attempt,
                event=make_event(
                    type_="done",
                    status=JOB_SUCCEEDED,
                    progress=100,
                    message="DXF 分类结果、报告和清单已登记",
                    run_id=run.id,
                    classified_count=run.classified_count,
                    review_required_count=run.review_required_count,
                    unreadable_count=run.unreadable_count,
                ),
            )
    except Exception as exc:
        db.rollback()
        if "attempt" in locals():
            try:
                _mark_failed(db, job_id, attempt, exc)
            except Exception:
                db.rollback()
                logger.exception("Failed to persist classifier failure for job %s", job_id)
        logger.exception("DXF classification failed for job %s", job_id)
    finally:
        db.close()
