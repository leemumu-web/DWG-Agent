"""Source, output and run ledgers for Steel DXF classification."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.modules.dxf_classification.adapter import (
    CLASSIFIER_VERSION,
    CLI_SCHEMA,
    ERROR_CODE_CLASSIFICATION_FAILED,
    REPORT_SCHEMA,
    ClassificationError,
)
from app.modules.dxf_classification.models import (
    DxfClassificationItem,
    DxfClassificationRun,
)
from app.modules.files.interface import (
    StoredFile,
    complete_transfer_in_transaction,
    prepare_generated_file_transfer,
    save_bytes_as_file,
)
from app.modules.jobs.interface import AnalysisResult, Job, fail_job_attempt
from app.modules.workflows.interface import WorkflowInputItem, WorkflowRun
from app.platform.config.constants import TASK_STEEL_DXF_CLASSIFICATION
from app.platform.config.settings import settings


def classification_request_id(*, job_id: int, attempt: int, relative_path: str) -> str:
    """Build a deterministic transfer request ID within the 64-character DB contract."""
    semantic_key = f"{job_id}:{attempt}:{relative_path}".encode()
    digest = hashlib.sha256(semantic_key).hexdigest()[:44]
    return f"dxf-classification:{digest}"


def classification_sources(
    db: Session, workflow: WorkflowRun
) -> list[tuple[WorkflowInputItem, StoredFile]]:
    """Resolve the immutable DXF source ledger from one frozen workflow input."""
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


def get_or_create_classification_run(
    db: Session,
    *,
    job: Job,
    workflow: WorkflowRun,
    attempt: int,
    project_name: str,
    manifest_sha256: str,
    input_count: int,
) -> DxfClassificationRun:
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
            input_manifest_sha256=manifest_sha256,
            input_count=input_count,
            started_at=datetime.now(UTC),
        )
        db.add(run)
        db.commit()
    return run


def load_classification_run(db: Session, *, job_id: int, attempt: int) -> DxfClassificationRun:
    run = db.scalar(
        select(DxfClassificationRun).where(
            DxfClassificationRun.job_id == job_id,
            DxfClassificationRun.job_attempt == attempt,
        )
    )
    if run is None:
        raise ClassificationError("分类运行账本不存在。")
    return run


def persist_output(
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
    """Store and register one routed DXF or classifier report."""
    payload = path.read_bytes()
    bucket = (
        settings.minio_bucket_dxf_derived
        if path.suffix.lower() == ".dxf"
        else settings.minio_bucket_reports
    )
    storage_key = (
        f"workflows/{workflow_id}/dxf-classification/attempt-{attempt}/{relative_path}"
    )
    request_id = classification_request_id(
        job_id=job.id,
        attempt=attempt,
        relative_path=relative_path,
    )
    transfer_uid = prepare_generated_file_transfer(
        db,
        actor_user_id=job.created_by,
        request_id=request_id,
        batch_ref=batch_name,
        bucket=bucket,
        storage_key=storage_key,
        original_name=path.name,
        expected_bytes=len(payload),
    )
    stored = save_bytes_as_file(
        db,
        bucket=bucket,
        storage_key=storage_key,
        original_name=path.name,
        file_ext=path.suffix.lower(),
        content_type=content_type,
        payload=payload,
        uploaded_by=job.created_by,
        batch_name=batch_name,
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
    return stored


def record_classification_item(
    db: Session,
    *,
    run: DxfClassificationRun,
    input_item: WorkflowInputItem,
    source_file: StoredFile,
    output_file: StoredFile,
    output_name: str,
    route: str,
    result: dict[str, Any],
) -> DxfClassificationItem:
    item = DxfClassificationItem(
        run=run,
        drawing_id=input_item.drawing_id,
        source_file_id=source_file.id,
        output_file_id=output_file.id,
        source_name=source_file.original_name,
        output_name=output_name,
        output_directory=route,
        disposition=str(result.get("disposition") or ""),
        part_type=(result.get("part_type") if isinstance(result.get("part_type"), str) else None),
        diagnostics_json=(
            result.get("diagnostics") if isinstance(result.get("diagnostics"), list) else []
        ),
        evidence_json={
            "candidates": result.get("candidates", []),
            "source_metadata": result.get("source_metadata", {}),
        },
    )
    db.add(item)
    return item


def record_classification_analysis(
    db: Session,
    *,
    job: Job,
    workflow_id: int,
    run: DxfClassificationRun,
    cli_payload: dict[str, Any],
    summary: dict[str, Any],
    report_file: StoredFile,
    manifest_file: StoredFile,
) -> AnalysisResult:
    analysis = AnalysisResult(
        job_id=job.id,
        result_type=TASK_STEEL_DXF_CLASSIFICATION,
        result_json={
            "workflow_id": workflow_id,
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
    return analysis


def finish_classification_run(
    run: DxfClassificationRun,
    *,
    cli_payload: dict[str, Any],
    summary: dict[str, Any],
    report_file: StoredFile,
    manifest_file: StoredFile,
) -> None:
    run.status = "completed_with_review" if cli_payload.get("exit_code") == 2 else "completed"
    run.report_schema = REPORT_SCHEMA
    run.cli_schema = CLI_SCHEMA
    run.classified_count = int(summary.get("classified_count") or 0)
    run.review_required_count = int(summary.get("review_required_count") or 0)
    run.unreadable_count = int(summary.get("unreadable_count") or 0)
    run.type_counts_json = (
        summary.get("type_counts") if isinstance(summary.get("type_counts"), dict) else {}
    )
    run.report_file_id = report_file.id
    run.manifest_file_id = manifest_file.id
    run.finished_at = datetime.now(UTC)


def mark_classification_failed(db: Session, job_id: int, attempt: int, exc: Exception) -> None:
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


def latest_classification_run(db: Session, workflow_id: int) -> DxfClassificationRun | None:
    return db.scalar(
        select(DxfClassificationRun)
        .where(DxfClassificationRun.workflow_run_id == workflow_id)
        .options(selectinload(DxfClassificationRun.items))
        .order_by(
            DxfClassificationRun.job_attempt.desc(),
            DxfClassificationRun.id.desc(),
        )
    )
