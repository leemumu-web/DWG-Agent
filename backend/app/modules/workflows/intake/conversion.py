"""Idempotent DWG-to-DXF Job planning and attempt-aware input synchronization."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, validate_dxf_structure
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    JobCreate,
    create_or_reuse_job,
    retry_job,
)
from app.modules.workflows.intake import registration
from app.modules.workflows.models import WorkflowInputBatch, WorkflowInputItem
from app.platform.config.constants import TASK_DWG_TO_DXF
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException


@dataclass(frozen=True)
class InputConversionPlan:
    jobs: list[Job]
    dispatch: list[tuple[int, int]]


def prepare_input_conversions(
    db: Session,
    batch: WorkflowInputBatch,
    *,
    created_by: int,
) -> InputConversionPlan:
    batch = registration.lock_input_batch(db, batch)
    if batch.status == "frozen":
        raise AppHTTPException(
            409, "INPUT_BATCH_FROZEN", "Frozen input batches cannot be modified."
        )
    if not settings.dxf_pipeline_enabled:
        raise AppHTTPException(
            503,
            "DXF_PIPELINE_DISABLED",
            "DWG→DXF pipeline is disabled. Set DXF_PIPELINE_ENABLED=true to enable.",
        )
    dwg_items = [item for item in batch.items if item.role == "source_dwg"]
    if not dwg_items:
        raise AppHTTPException(
            409,
            "INPUT_DWG_REQUIRED",
            "Upload at least one valid DWG before starting conversion.",
        )
    excel_items = [
        item
        for item in batch.items
        if item.role == "source_excel"
        and item.status == "uploaded"
        and isinstance(item.validation_json, dict)
        and isinstance(item.validation_json.get("inspection"), dict)
    ]
    if len(excel_items) != 1:
        raise AppHTTPException(
            409,
            "INPUT_EXCEL_REQUIRED",
            "Upload one validated .xls or .xlsx before starting DWG conversion.",
        )
    jobs: list[Job] = []
    dispatch: list[tuple[int, int]] = []
    for item in dwg_items:
        stored = db.get(StoredFile, item.file_id)
        if stored is None or stored.status == "deleted":
            raise AppHTTPException(
                409,
                "INPUT_FILE_NOT_FOUND",
                "A registered DWG input is no longer available.",
                {"item_id": item.id, "file_id": item.file_id},
            )
        payload = JobCreate(
            project_id=batch.project_id,
            task_type=TASK_DWG_TO_DXF,
            params={"file_id": stored.id, "batch_name": stored.batch_name},
        )
        job, reused = create_or_reuse_job(
            db,
            payload,
            created_by=created_by,
            request_key=f"workflow-input-{batch.id}-{stored.id}",
        )
        should_dispatch = not reused
        if reused and job.status in {"failed", "cancelled"}:
            job = retry_job(db, job)
            should_dispatch = True
        item.conversion_job_id = job.id
        item.conversion_job_attempt = job.attempt
        item.status = "converted" if job.status == "succeeded" else "converting"
        item.error_code = None
        item.error_message = None
        jobs.append(job)
        if should_dispatch:
            dispatch.append((job.id, job.attempt))
    batch.status = "converting"
    batch.error_code = None
    batch.error_message = None
    db.flush()
    return InputConversionPlan(jobs=jobs, dispatch=dispatch)


def _mark_item_error(item: WorkflowInputItem, code: str, message: str) -> None:
    item.status = "conversion_failed"
    item.error_code = code
    item.error_message = message
    item.derived_dxf_file_id = None


def sync_input_batch(db: Session, batch: WorkflowInputBatch) -> WorkflowInputBatch:
    if batch.status == "frozen":
        return batch
    dwg_items = [item for item in batch.items if item.role == "source_dwg"]
    for item in dwg_items:
        if item.conversion_job_id is None:
            item.status = "uploaded"
            continue
        job = db.get(Job, item.conversion_job_id)
        if job is None or job.attempt != item.conversion_job_attempt:
            _mark_item_error(
                item,
                "INPUT_CONVERSION_GENERATION_MISMATCH",
                "The bound conversion attempt is no longer current. Submit conversion again.",
            )
            continue
        if job.status in registration.ACTIVE_INPUT_JOB_STATUSES:
            item.status = "converting"
            item.error_code = None
            item.error_message = None
            continue
        if job.status in {"failed", "cancelled"}:
            _mark_item_error(
                item,
                job.error_code or "INPUT_CONVERSION_FAILED",
                job.error_message or "DWG conversion did not complete. Retry this input.",
            )
            continue
        if job.status != "succeeded":
            _mark_item_error(
                item,
                "INPUT_CONVERSION_STATUS_INVALID",
                f"Unexpected conversion status: {job.status}.",
            )
            continue
        result = db.scalar(
            select(AnalysisResult)
            .where(
                AnalysisResult.job_id == job.id,
                AnalysisResult.status == "succeeded",
                AnalysisResult.result_type == TASK_DWG_TO_DXF,
            )
            .order_by(AnalysisResult.id.desc())
        )
        if result is None or result.result_file_id is None:
            _mark_item_error(
                item,
                "INPUT_DXF_RESULT_MISSING",
                "The successful conversion has no registered DXF result.",
            )
            continue
        result_json = result.result_json if isinstance(result.result_json, dict) else {}
        if result_json.get("source_file_id") != item.file_id:
            _mark_item_error(
                item,
                "INPUT_DXF_SOURCE_MISMATCH",
                "The conversion result is not bound to this source DWG.",
            )
            continue
        if result_json.get("dxf_file_id") != result.result_file_id:
            _mark_item_error(
                item,
                "INPUT_DXF_RESULT_MISMATCH",
                "The conversion result metadata does not match its registered DXF file.",
            )
            continue
        derived = db.get(StoredFile, result.result_file_id)
        if derived is None or derived.status == "deleted" or derived.file_ext.lower() != ".dxf":
            _mark_item_error(
                item,
                "INPUT_DXF_FILE_INVALID",
                "The conversion result is not an available DXF file.",
            )
            continue
        if registration.normalize_input_stem(derived.original_name) != item.normalized_stem:
            _mark_item_error(
                item,
                "INPUT_DXF_NAME_MISMATCH",
                "The server-derived DXF name does not match its source DWG.",
            )
            continue
        payload = registration.read_verified_input_object(derived)
        try:
            validate_dxf_structure(payload)
        except AppHTTPException:
            _mark_item_error(
                item,
                "INPUT_DXF_UNREADABLE",
                "The server-derived DXF does not contain a readable DXF structure.",
            )
            continue
        item.derived_dxf_file_id = derived.id
        item.status = "paired"
        item.error_code = None
        item.error_message = None

    stems: dict[str, list[WorkflowInputItem]] = {}
    for item in dwg_items:
        stems.setdefault(item.normalized_stem, []).append(item)
    for conflict_items in stems.values():
        if len(conflict_items) > 1:
            for item in conflict_items:
                _mark_item_error(
                    item,
                    "INPUT_DWG_NAME_CONFLICT",
                    "Multiple DWG inputs normalize to the same drawing name.",
                )

    excel_items = [item for item in batch.items if item.role == "source_excel"]
    excel_ready = len(excel_items) == 1 and excel_items[0].status in {"uploaded", "frozen"}
    if dwg_items and excel_ready and all(item.status == "paired" for item in dwg_items):
        batch.status = "ready_to_freeze"
        batch.error_code = None
        batch.error_message = None
    elif any(
        item.status in {"conversion_failed", "failed"}
        for item in batch.items
    ):
        batch.status = "needs_attention"
        batch.error_code = "INPUT_BATCH_NEEDS_ATTENTION"
        batch.error_message = "Resolve the file-level input issues before freezing."
    elif any(item.status == "converting" for item in dwg_items):
        batch.status = "converting"
    else:
        batch.status = "uploading"
    db.flush()
    return batch
