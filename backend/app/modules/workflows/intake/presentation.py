"""Operator-facing production input counts, diagnostics and next actions."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.files.interface import FileRead, StoredFile
from app.modules.jobs.interface import Job, JobRead
from app.modules.workflows.intake.conversion import sync_input_batch
from app.modules.workflows.intake.recovery import recoverable_input_file_ids
from app.modules.workflows.models import WorkflowInputBatch
from app.modules.workflows.schemas import (
    WorkflowInputBatchRead,
    WorkflowInputCounts,
    WorkflowInputIssueRead,
    WorkflowInputItemRead,
)
from app.platform.http.exceptions import AppHTTPException

INPUT_BATCH_SYNC_LIMIT = 100


def describe_input_batch(
    db: Session,
    batch: WorkflowInputBatch,
    *,
    item_page: int = 1,
    item_page_size: int = 50,
) -> WorkflowInputBatchRead:
    sync_input_batch(db, batch, max_terminal_items=INPUT_BATCH_SYNC_LIMIT)
    dwg_items = [item for item in batch.items if item.role == "source_dwg"]
    excel_items = [item for item in batch.items if item.role == "source_excel"]
    item_total = len(batch.items)
    item_start = (item_page - 1) * item_page_size
    visible_items = batch.items[item_start : item_start + item_page_size]
    issues: list[WorkflowInputIssueRead] = []
    if not dwg_items:
        issues.append(
            WorkflowInputIssueRead(
                code="INPUT_DWG_REQUIRED",
                message="至少上传一个有效 DWG。",
                recommended_action="upload_dwg",
            )
        )
    if len(excel_items) != 1:
        issues.append(
            WorkflowInputIssueRead(
                code="INPUT_EXCEL_COUNT_INVALID",
                message="必须上传且只能上传一个可读 Excel。",
                recommended_action="upload_excel" if not excel_items else "remove_excel",
            )
        )
    item_reads: list[WorkflowInputItemRead] = []
    for item in visible_items:
        stored = db.get(StoredFile, item.file_id)
        if stored is None:
            raise AppHTTPException(
                409,
                "INPUT_FILE_NOT_FOUND",
                "A registered input file no longer exists.",
                {"item_id": item.id, "file_id": item.file_id},
            )
        job = db.get(Job, item.conversion_job_id) if item.conversion_job_id else None
        derived = db.get(StoredFile, item.derived_dxf_file_id) if item.derived_dxf_file_id else None
        if item.error_code:
            failure = None
            if isinstance(item.validation_json, dict):
                candidate = item.validation_json.get("failure")
                if isinstance(candidate, dict):
                    failure = candidate
            issues.append(
                WorkflowInputIssueRead(
                    item_id=item.id,
                    file_name=item.original_name,
                    code=item.error_code,
                    message=item.error_message or "输入文件需要处理。",
                    recommended_action=(
                        "retry_conversion" if item.role == "source_dwg" else "remove_file"
                    ),
                    failure=failure,
                )
            )
        item_reads.append(
            WorkflowInputItemRead(
                id=item.id,
                role=item.role,
                status=item.status,
                original_name=item.original_name,
                normalized_stem=item.normalized_stem,
                file=FileRead.model_validate(stored),
                conversion_job=JobRead.model_validate(job) if job else None,
                derived_dxf=FileRead.model_validate(derived) if derived else None,
                drawing_id=item.drawing_id,
                error_code=item.error_code,
                error_message=item.error_message,
                validation=item.validation_json,
                validation_contract_version=item.validation_contract_version,
                validated_sha256=item.validated_sha256,
            )
        )
    counts = WorkflowInputCounts(
        dwg=len(dwg_items),
        excel=len(excel_items),
        paired=sum(item.status in {"paired", "frozen"} for item in dwg_items),
        converting=sum(item.status == "converting" for item in dwg_items),
        failed=sum(
            item.status in {"conversion_failed", "failed"}
            for item in batch.items
        ),
    )
    return WorkflowInputBatchRead(
        id=batch.id,
        workflow_run_id=batch.workflow_run_id,
        project_id=batch.project_id,
        status=batch.status,
        version=batch.version,
        manifest_sha256=batch.manifest_sha256,
        frozen_at=batch.frozen_at,
        counts=counts,
        items=item_reads,
        item_total=item_total,
        item_page=item_page,
        item_page_size=item_page_size,
        issues=issues,
        freeze_ready=batch.status in {"ready_to_freeze", "frozen"},
        recoverable_file_count=(
            0 if batch.items else len(recoverable_input_file_ids(db, batch))
        ),
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )
