from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import openpyxl
import xlrd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import TASK_DWG_TO_DXF
from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile
from app.models.job import Job
from app.models.result import AnalysisResult
from app.models.workflow import WorkflowRun
from app.models.workflow_input import WorkflowInputBatch, WorkflowInputItem
from app.schemas.job_schema import JobCreate
from app.services.job_service import create_or_reuse_job, retry_job
from app.services.storage_service import (
    MIN_DWG_SIZE_BYTES,
    get_storage_backend,
    validate_dwg_header,
)
from app.storage.base import StorageError, StorageObjectNotFound

_WHITESPACE = re.compile(r"\s+")
_ACTIVE_JOB_STATUSES = {
    "pending",
    "queued",
    "running",
    "validating",
    "waiting_cad_worker",
}


@dataclass(frozen=True)
class InputConversionPlan:
    jobs: list[Job]
    dispatch: list[tuple[int, int]]


def normalize_input_stem(name: str) -> str:
    basename = Path(name.replace("\\", "/")).name
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    normalized = unicodedata.normalize("NFKC", stem).strip()
    return _WHITESPACE.sub(" ", normalized).casefold()


def create_input_batch(
    db: Session,
    workflow: WorkflowRun,
    *,
    created_by: int,
) -> WorkflowInputBatch:
    if workflow.workflow_type != "linux_production":
        raise AppHTTPException(
            422,
            "INPUT_BATCH_WORKFLOW_TYPE_INVALID",
            "Input batches are only available for linux_production workflows.",
        )
    existing = db.scalar(
        select(WorkflowInputBatch).where(
            WorkflowInputBatch.workflow_run_id == workflow.id
        )
    )
    if existing is not None:
        return existing
    batch = WorkflowInputBatch(
        workflow=workflow,
        project_id=workflow.project_id,
        created_by=created_by,
        status="uploading",
        version=1,
    )
    db.add(batch)
    db.flush()
    return batch


def _read_verified_object(stored: StoredFile) -> bytes:
    if stored.status == "deleted":
        raise AppHTTPException(404, "INPUT_FILE_NOT_FOUND", "Input file is unavailable.")
    storage = get_storage_backend()
    digest = hashlib.sha256()
    payload = bytearray()
    maximum = settings.max_upload_size_mb * 1024 * 1024
    try:
        for chunk in storage.iter_file(stored.bucket, stored.storage_key):
            payload.extend(chunk)
            if len(payload) > maximum:
                raise AppHTTPException(
                    413,
                    "INPUT_OBJECT_TOO_LARGE",
                    "Input object exceeds the configured upload limit.",
                )
            digest.update(chunk)
    except AppHTTPException:
        raise
    except StorageObjectNotFound as exc:
        raise AppHTTPException(
            409,
            "INPUT_OBJECT_MISSING",
            "The registered input object is missing from storage.",
        ) from exc
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "INPUT_OBJECT_READ_FAILED",
            "The input object could not be read from storage.",
        ) from exc
    if len(payload) != stored.size_bytes:
        raise AppHTTPException(
            409,
            "INPUT_OBJECT_SIZE_MISMATCH",
            "The input object size does not match its file registration.",
        )
    if digest.hexdigest() != stored.sha256:
        raise AppHTTPException(
            409,
            "INPUT_OBJECT_CHECKSUM_MISMATCH",
            "The input object checksum does not match its file registration.",
        )
    return bytes(payload)


def _validate_dwg(payload: bytes) -> None:
    validate_dwg_header(payload[:6])
    if len(payload) < MIN_DWG_SIZE_BYTES:
        raise AppHTTPException(
            415,
            "FILE_NOT_DWG",
            f"File too small ({len(payload)} bytes) — legitimate DWG files exceed {MIN_DWG_SIZE_BYTES} bytes.",
        )


def _validate_excel(file_ext: str, payload: bytes) -> None:
    try:
        if file_ext == ".xlsx":
            workbook = openpyxl.load_workbook(BytesIO(payload), read_only=True, data_only=True)
            try:
                visible_count = sum(
                    1 for sheet in workbook.worksheets if sheet.sheet_state == "visible"
                )
            finally:
                workbook.close()
        else:
            workbook = xlrd.open_workbook(file_contents=payload, on_demand=True)
            try:
                visible_count = sum(
                    1
                    for sheet in workbook.sheets()
                    if getattr(sheet, "visibility", 0) == 0
                )
            finally:
                workbook.release_resources()
    except Exception as exc:
        raise AppHTTPException(
            415,
            "INPUT_EXCEL_UNREADABLE",
            "The Excel file is damaged, encrypted, or does not match its extension.",
        ) from exc
    if visible_count < 1:
        raise AppHTTPException(
            415,
            "INPUT_EXCEL_NO_VISIBLE_SHEET",
            "The Excel file must contain at least one visible worksheet.",
        )


def register_input_file(
    db: Session,
    batch: WorkflowInputBatch,
    stored: StoredFile,
) -> WorkflowInputItem:
    if batch.status == "frozen":
        raise AppHTTPException(
            409, "INPUT_BATCH_FROZEN", "Frozen input batches cannot be modified."
        )
    existing = next((item for item in batch.items if item.file_id == stored.id), None)
    if existing is not None:
        return existing
    file_ext = stored.file_ext.lower()
    if file_ext == ".dxf":
        raise AppHTTPException(
            422,
            "INPUT_DXF_NOT_ALLOWED",
            "DXF must be generated by the server from a registered DWG input.",
        )
    if file_ext not in {".dwg", ".xls", ".xlsx"}:
        raise AppHTTPException(
            422,
            "INPUT_FILE_TYPE_NOT_ALLOWED",
            "Production input accepts DWG files and exactly one Excel file.",
        )
    role = "source_dwg" if file_ext == ".dwg" else "source_excel"
    if role == "source_excel" and any(
        item.role == "source_excel" for item in batch.items
    ):
        raise AppHTTPException(
            409,
            "INPUT_EXCEL_ALREADY_EXISTS",
            "The input batch already contains an Excel file. Remove it before replacement.",
        )
    payload = _read_verified_object(stored)
    if role == "source_dwg":
        _validate_dwg(payload)
    else:
        _validate_excel(file_ext, payload)
    item = WorkflowInputItem(
        batch=batch,
        file_id=stored.id,
        role=role,
        original_name=stored.original_name,
        normalized_stem=normalize_input_stem(stored.original_name),
        status="uploaded",
    )
    db.add(item)
    db.flush()
    return item


def prepare_input_conversions(
    db: Session,
    batch: WorkflowInputBatch,
    *,
    created_by: int,
) -> InputConversionPlan:
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
        if job.status in _ACTIVE_JOB_STATUSES:
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
        derived = db.get(StoredFile, result.result_file_id)
        if derived is None or derived.status == "deleted" or derived.file_ext.lower() != ".dxf":
            _mark_item_error(
                item,
                "INPUT_DXF_FILE_INVALID",
                "The conversion result is not an available DXF file.",
            )
            continue
        if normalize_input_stem(derived.original_name) != item.normalized_stem:
            _mark_item_error(
                item,
                "INPUT_DXF_NAME_MISMATCH",
                "The server-derived DXF name does not match its source DWG.",
            )
            continue
        payload = _read_verified_object(derived)
        if b"SECTION" not in payload[:4096] or b"EOF" not in payload[-4096:]:
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

    excel_count = sum(item.role == "source_excel" for item in batch.items)
    if dwg_items and excel_count == 1 and all(item.status == "paired" for item in dwg_items):
        batch.status = "ready_to_freeze"
        batch.error_code = None
        batch.error_message = None
    elif any(item.status == "conversion_failed" for item in dwg_items):
        batch.status = "needs_attention"
        batch.error_code = "INPUT_BATCH_NEEDS_ATTENTION"
        batch.error_message = "Resolve the file-level input issues before freezing."
    elif any(item.status == "converting" for item in dwg_items):
        batch.status = "converting"
    else:
        batch.status = "uploading"
    db.flush()
    return batch
