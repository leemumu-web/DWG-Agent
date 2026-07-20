from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.dxf_classification import DxfClassificationItem, DxfClassificationRun
from app.models.file import StoredFile
from app.models.job import Job, JobStep
from app.models.result import AnalysisResult
from app.models.workflow import WorkflowRun
from app.models.workflow_input import WorkflowInputBatch, WorkflowInputItem
from app.modules.projects.interface import Project
from app.platform.config.constants import (
    JOB_RUNNING,
    JOB_SUCCEEDED,
    PIPELINE_STEEL_DXF_CLASSIFIER,
    STEP_PERSIST_CLASSIFICATION,
    STEP_RUN_STEEL_DXF_CLASSIFIER,
    STEP_STAGE_CLASSIFIER_INPUT,
    TASK_STEEL_DXF_CLASSIFICATION,
)
from app.platform.config.settings import settings
from app.platform.database.session import SessionLocal
from app.services.job_events import make_event
from app.services.job_service import (
    claim_queued_job,
    commit_job_progress,
    complete_job_attempt,
    fail_job_attempt,
)
from app.services.storage_service import save_bytes_as_file
from app.services.workflow_input_service import _read_verified_object
from app.services.workflow_service import attach_artifact

logger = logging.getLogger(__name__)

CLASSIFIER_VERSION = "1.1.0"
REPORT_SCHEMA = "STEEL-DXF-CLASSIFICATION-1.1"
CLI_SCHEMA = "STEEL-DXF-CLI-1.1"
ERROR_CODE_CLASSIFICATION_FAILED = "DXF_CLASSIFICATION_FAILED"
ERROR_CODE_CLASSIFICATION_CONTRACT = "DXF_CLASSIFICATION_CONTRACT_INVALID"


class ClassificationError(RuntimeError):
    pass


def classifier_project_name(project_code: str, workflow_id: int) -> str:
    return f"{project_code}-workflow-{workflow_id}"


def _preprocessed_name(name: str) -> str:
    source = Path(name).name
    if Path(source).suffix.lower() != ".dxf":
        raise ClassificationError(f"分类输入不是 DXF: {source}")
    stem = Path(source).stem
    if not stem.endswith("_拆板前"):
        stem = f"{stem}_拆板前"
    return f"{stem}.dxf"


def _invoke_classifier(input_directory: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "steel_dxf_classifier.cli",
                "--json",
                str(input_directory),
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=settings.dxf_classification_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClassificationError("DXF 分类器执行超时。") from exc
    if completed.returncode not in {0, 2}:
        message = completed.stderr.strip() or "DXF 分类器执行失败。"
        raise ClassificationError(message.removeprefix("错误: ").strip())
    if completed.stderr.strip():
        raise ClassificationError("DXF 分类器成功退出时产生了非预期 stderr。")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ClassificationError("DXF 分类器未返回合法 JSON。") from exc
    if payload.get("schema") != CLI_SCHEMA or payload.get("exit_code") != completed.returncode:
        raise ClassificationError("DXF 分类器 CLI schema 或退出码不符合 1.1 契约。")
    return payload


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


def _classification_sources(db: Session, workflow: WorkflowRun) -> list[tuple[WorkflowInputItem, StoredFile]]:
    batch = workflow.input_batch
    if batch is None or batch.status != "frozen" or not batch.manifest_sha256:
        raise ClassificationError("生产输入批次尚未冻结。")
    sources: list[tuple[WorkflowInputItem, StoredFile]] = []
    for item in batch.items:
        if item.role != "source_dwg":
            continue
        if item.derived_dxf_file_id is None:
            raise ClassificationError(f"输入条目 {item.id} 缺少服务器派生 DXF。")
        stored = db.get(StoredFile, item.derived_dxf_file_id)
        if stored is None or stored.status == "deleted":
            raise ClassificationError(f"输入条目 {item.id} 的派生 DXF 不可用。")
        sources.append((item, stored))
    if not sources:
        raise ClassificationError("冻结批次中没有可分类的 DXF。")
    return sources


def _safe_route(project_name: str, route: object) -> str:
    if not isinstance(route, str) or Path(route).name != route:
        raise ClassificationError("分类报告包含非法输出目录。")
    if not route.startswith(f"{project_name}_") or not route.endswith("_dxf"):
        raise ClassificationError("分类输出目录不符合 1.1 命名契约。")
    return route


def _persist_output(
    db: Session,
    *,
    job: Job,
    workflow_id: int,
    attempt: int,
    relative_path: str,
    path: Path,
    batch_name: str,
    content_type: str,
) -> StoredFile:
    return save_bytes_as_file(
        db,
        bucket=settings.minio_bucket_dxf_derived if path.suffix.lower() == ".dxf" else settings.minio_bucket_reports,
        storage_key=f"workflows/{workflow_id}/dxf-classification/attempt-{attempt}/{relative_path}",
        original_name=path.name,
        file_ext=path.suffix.lower(),
        content_type=content_type,
        payload=path.read_bytes(),
        uploaded_by=job.created_by,
        batch_name=batch_name,
        request_id=f"dxf-classification:{job.id}:{attempt}:{relative_path}",
    )


def _mark_failed(db: Session, job_id: int, attempt: int, exc: Exception) -> None:
    message = str(exc) or exc.__class__.__name__
    run = db.scalar(
        select(DxfClassificationRun).where(
            DxfClassificationRun.job_id == job_id,
            DxfClassificationRun.job_attempt == attempt,
        )
    )
    if run is not None:
        run.status = "failed"
        run.error_code = ERROR_CODE_CLASSIFICATION_FAILED
        run.error_message = message
        run.finished_at = datetime.now(UTC)
    fail_job_attempt(
        db,
        job_id,
        attempt=attempt,
        error_code=ERROR_CODE_CLASSIFICATION_FAILED,
        error_message=message,
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
        run = db.scalar(
            select(DxfClassificationRun).where(
                DxfClassificationRun.job_id == job.id,
                DxfClassificationRun.job_attempt == attempt,
            )
        )
        if run is None:
            run = DxfClassificationRun(
                workflow_run_id=workflow.id,
                project_id=workflow.project_id,
                job_id=job.id,
                job_attempt=attempt,
                status="running",
                classifier_version=CLASSIFIER_VERSION,
                project_name=project_name,
                input_manifest_sha256=batch.manifest_sha256,
                input_count=len(sources),
                started_at=datetime.now(UTC),
            )
            db.add(run)
            db.commit()

        with tempfile.TemporaryDirectory(prefix=f"dxf-classification-{job.id}-{attempt}-") as raw_root:
            root = Path(raw_root)
            input_directory = root / f"{project_name}_dxf"
            input_directory.mkdir()
            by_output_name: dict[str, tuple[WorkflowInputItem, StoredFile, bytes]] = {}
            for item, stored in sources:
                name = Path(stored.original_name).name
                payload = _read_verified_object(stored)
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
                event=make_event(type_="progress", status=JOB_RUNNING, progress=25, message="分类输入已校验"),
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
            if not isinstance(results, list) or not isinstance(summary, dict) or len(results) != len(sources):
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
                event=make_event(type_="progress", status=JOB_RUNNING, progress=70, message="DXF 分类分流完成"),
            )
            if job is None:
                return

            run = db.scalar(
                select(DxfClassificationRun).where(
                    DxfClassificationRun.job_id == job.id,
                    DxfClassificationRun.job_attempt == attempt,
                )
            )
            assert run is not None
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
                if hashlib.sha256(output_payload).hexdigest() != hashlib.sha256(source_payload).hexdigest():
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
                db.add(
                    DxfClassificationItem(
                        run=run,
                        drawing_id=input_item.drawing_id,
                        source_file_id=source_file.id,
                        output_file_id=output_file.id,
                        source_name=source_file.original_name,
                        output_name=output_name,
                        output_directory=route,
                        disposition=str(result.get("disposition") or ""),
                        part_type=result.get("part_type") if isinstance(result.get("part_type"), str) else None,
                        diagnostics_json=result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else [],
                        evidence_json={
                            "candidates": result.get("candidates", []),
                            "source_metadata": result.get("source_metadata", {}),
                        },
                    )
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
            analysis = AnalysisResult(
                job_id=job.id,
                result_type=TASK_STEEL_DXF_CLASSIFICATION,
                result_json={
                    "workflow_id": workflow.id,
                    "run_id": run.id,
                    "workflow_artifact_type": "classification_report",
                    "cli": cli_payload,
                    "summary": summary,
                    "manifest_file_id": manifest_file.id,
                },
                confidence=Decimal("1.0000"),
                result_file_id=report_file.id,
                algorithm_version=CLASSIFIER_VERSION,
                tool_version="steel-dxf-classifier",
                status="succeeded",
            )
            db.add(analysis)
            db.flush()
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
            run.status = "completed_with_review" if cli_payload.get("exit_code") == 2 else "completed"
            run.report_schema = REPORT_SCHEMA
            run.cli_schema = CLI_SCHEMA
            run.classified_count = int(summary.get("classified_count") or 0)
            run.review_required_count = int(summary.get("review_required_count") or 0)
            run.unreadable_count = int(summary.get("unreadable_count") or 0)
            run.type_counts_json = summary.get("type_counts") if isinstance(summary.get("type_counts"), dict) else {}
            run.report_file_id = report_file.id
            run.manifest_file_id = manifest_file.id
            run.finished_at = datetime.now(UTC)
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


def latest_classification_run(db: Session, workflow_id: int) -> DxfClassificationRun | None:
    return db.scalar(
        select(DxfClassificationRun)
        .where(DxfClassificationRun.workflow_run_id == workflow_id)
        .options(selectinload(DxfClassificationRun.items))
        .order_by(DxfClassificationRun.job_attempt.desc(), DxfClassificationRun.id.desc())
    )
