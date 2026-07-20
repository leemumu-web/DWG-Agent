from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import openpyxl
import xlrd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.workflow import WorkflowRun
from app.models.workflow_input import WorkflowInputBatch, WorkflowInputItem
from app.modules.files.interface import (
    MIN_DWG_SIZE_BYTES,
    FileRead,
    StoredFile,
    get_storage_backend,
    validate_dwg_header,
)
from app.modules.jobs.interface import (
    AnalysisResult,
    Job,
    JobCreate,
    JobRead,
    cancel_job,
    create_or_reuse_job,
    retry_job,
)
from app.modules.projects.interface import Drawing, DrawingVersion
from app.platform.config.constants import TASK_DWG_TO_DXF
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import StorageError, StorageObjectNotFound
from app.schemas.workflow_input_schema import (
    WorkflowInputBatchRead,
    WorkflowInputCounts,
    WorkflowInputIssueRead,
    WorkflowInputItemRead,
)

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
    try:
        with db.begin_nested():
            batch = WorkflowInputBatch(
                workflow=workflow,
                project_id=workflow.project_id,
                created_by=created_by,
                status="uploading",
                version=1,
            )
            db.add(batch)
            db.flush()
    except IntegrityError:
        winner = db.scalar(
            select(WorkflowInputBatch)
            .where(WorkflowInputBatch.workflow_run_id == workflow.id)
            .with_for_update()
        )
        if winner is None:
            raise
        return winner
    return batch


def _lock_input_batch(db: Session, batch: WorkflowInputBatch) -> WorkflowInputBatch:
    """Serialize mutable input operations and refresh their item collection."""
    locked = db.scalar(
        select(WorkflowInputBatch)
        .where(WorkflowInputBatch.id == batch.id)
        .with_for_update()
    )
    if locked is None:
        raise AppHTTPException(404, "INPUT_BATCH_NOT_FOUND", "Input batch not found.")
    db.expire(locked, ["items"])
    return locked


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
    batch = _lock_input_batch(db, batch)
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


def get_input_batch(db: Session, workflow_id: int) -> WorkflowInputBatch:
    batch = db.scalar(
        select(WorkflowInputBatch).where(
            WorkflowInputBatch.workflow_run_id == workflow_id
        )
    )
    if batch is None:
        raise AppHTTPException(404, "INPUT_BATCH_NOT_FOUND", "Input batch not found.")
    return batch


def remove_input_item(
    db: Session,
    batch: WorkflowInputBatch,
    item_id: int,
) -> None:
    batch = _lock_input_batch(db, batch)
    if batch.status == "frozen":
        raise AppHTTPException(
            409, "INPUT_BATCH_FROZEN", "Frozen input batches cannot be modified."
        )
    item = next((value for value in batch.items if value.id == item_id), None)
    if item is None:
        raise AppHTTPException(404, "INPUT_ITEM_NOT_FOUND", "Input item not found.")
    if item.conversion_job_id is not None:
        job = db.get(Job, item.conversion_job_id)
        if job is not None and job.status in _ACTIVE_JOB_STATUSES:
            cancel_job(db, job)
    db.delete(item)
    db.flush()
    batch.status = "uploading"
    batch.error_code = None
    batch.error_message = None


def prepare_input_conversions(
    db: Session,
    batch: WorkflowInputBatch,
    *,
    created_by: int,
) -> InputConversionPlan:
    batch = _lock_input_batch(db, batch)
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


def _display_input_stem(name: str) -> str:
    basename = Path(name.replace("\\", "/")).name
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", stem).strip())


def _manifest_entry(stored: StoredFile) -> dict[str, object]:
    return {
        "file_id": stored.id,
        "original_name": stored.original_name,
        "size_bytes": stored.size_bytes,
        "sha256": stored.sha256,
    }


def freeze_input_batch(
    db: Session,
    batch: WorkflowInputBatch,
) -> WorkflowInputBatch:
    from app.services import workflow_service

    locked = db.scalar(
        select(WorkflowInputBatch)
        .where(WorkflowInputBatch.id == batch.id)
        .with_for_update()
    )
    if locked is None:
        raise AppHTTPException(404, "INPUT_BATCH_NOT_FOUND", "Input batch not found.")
    batch = locked
    if batch.status == "frozen":
        return batch
    workflow = batch.workflow
    if workflow.status in {"succeeded", "failed", "cancelled"}:
        raise AppHTTPException(
            409,
            "INPUT_BATCH_WORKFLOW_TERMINAL",
            "A terminal workflow cannot freeze new input.",
        )
    if workflow.current_stage != "source_intake":
        raise AppHTTPException(
            409,
            "INPUT_BATCH_STAGE_INVALID",
            "Start the workflow and keep it at source_intake before freezing.",
        )

    dwg_items = [item for item in batch.items if item.role == "source_dwg"]
    excel_items = [item for item in batch.items if item.role == "source_excel"]
    if not dwg_items:
        raise AppHTTPException(
            409, "INPUT_DWG_REQUIRED", "At least one DWG input is required."
        )
    if len(excel_items) != 1:
        raise AppHTTPException(
            409,
            "INPUT_EXCEL_COUNT_INVALID",
            "Exactly one readable Excel input is required.",
            {"excel_count": len(excel_items)},
        )
    stems: dict[str, list[WorkflowInputItem]] = {}
    for item in dwg_items:
        stems.setdefault(item.normalized_stem, []).append(item)
    conflicts = [items for items in stems.values() if len(items) > 1]
    if conflicts:
        raise AppHTTPException(
            409,
            "INPUT_DWG_NAME_CONFLICT",
            "Multiple DWG files normalize to the same drawing name.",
            {
                "conflicts": [
                    [item.original_name for item in conflict] for conflict in conflicts
                ]
            },
        )

    for item in batch.items:
        stored = db.get(StoredFile, item.file_id)
        if stored is None:
            raise AppHTTPException(
                409,
                "INPUT_FILE_NOT_FOUND",
                "A registered input file no longer exists.",
                {"item_id": item.id, "file_id": item.file_id},
            )
        payload = _read_verified_object(stored)
        if item.role == "source_dwg":
            _validate_dwg(payload)
        else:
            _validate_excel(stored.file_ext.lower(), payload)

    sync_input_batch(db, batch)
    if batch.status != "ready_to_freeze":
        issues = [
            {
                "item_id": item.id,
                "file_name": item.original_name,
                "code": item.error_code,
                "message": item.error_message,
            }
            for item in dwg_items
            if item.status != "paired"
        ]
        raise AppHTTPException(
            409,
            "INPUT_BATCH_NOT_READY",
            "Every DWG must have one verified server-derived DXF before freezing.",
            {"issues": issues},
        )

    manifest_drawings: list[dict[str, object]] = []
    for item in sorted(dwg_items, key=lambda value: (value.normalized_stem, value.file_id)):
        source = db.get(StoredFile, item.file_id)
        derived = db.get(StoredFile, item.derived_dxf_file_id)
        assert source is not None and derived is not None
        drawing = Drawing(
            project_id=batch.project_id,
            drawing_no=_display_input_stem(item.original_name),
            title=_display_input_stem(item.original_name),
            discipline=None,
            status="active",
        )
        db.add(drawing)
        db.flush()
        version = DrawingVersion(
            drawing_id=drawing.id,
            file_id=source.id,
            version_no=1,
            source="workflow_input_dwg",
            created_by=batch.created_by,
        )
        db.add(version)
        db.flush()
        drawing.current_version_id = version.id
        item.drawing_id = drawing.id
        manifest_drawings.append(
            {
                "drawing_id": drawing.id,
                "normalized_stem": item.normalized_stem,
                "dwg": _manifest_entry(source),
                "dxf": _manifest_entry(derived),
            }
        )
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type="source_file",
            file_id=source.id,
            metadata={"batch_id": batch.id, "drawing_id": drawing.id},
        )
        workflow_service.attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type="derived_dxf",
            file_id=derived.id,
            metadata={"batch_id": batch.id, "drawing_id": drawing.id},
        )

    excel_item = excel_items[0]
    excel = db.get(StoredFile, excel_item.file_id)
    assert excel is not None
    workflow_service.attach_artifact(
        db,
        workflow,
        stage_code="source_intake",
        artifact_type="source_excel",
        file_id=excel.id,
        metadata={"batch_id": batch.id},
    )
    manifest = {
        "version": batch.version,
        "batch_id": batch.id,
        "workflow_id": workflow.id,
        "project_id": batch.project_id,
        "excel": _manifest_entry(excel),
        "drawings": manifest_drawings,
    }
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    batch.manifest_sha256 = hashlib.sha256(canonical).hexdigest()
    batch.status = "frozen"
    batch.frozen_at = datetime.now(UTC)
    batch.error_code = None
    batch.error_message = None
    for item in batch.items:
        item.status = "frozen"
        item.error_code = None
        item.error_message = None
    workflow_service.complete_manual_stage(workflow, "source_intake")
    db.flush()
    return batch


def describe_input_batch(
    db: Session,
    batch: WorkflowInputBatch,
) -> WorkflowInputBatchRead:
    sync_input_batch(db, batch)
    dwg_items = [item for item in batch.items if item.role == "source_dwg"]
    excel_items = [item for item in batch.items if item.role == "source_excel"]
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
    for item in batch.items:
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
            issues.append(
                WorkflowInputIssueRead(
                    item_id=item.id,
                    file_name=item.original_name,
                    code=item.error_code,
                    message=item.error_message or "输入文件需要处理。",
                    recommended_action=(
                        "retry_conversion"
                        if item.role == "source_dwg"
                        else "remove_file"
                    ),
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
            )
        )
    counts = WorkflowInputCounts(
        dwg=len(dwg_items),
        excel=len(excel_items),
        paired=sum(item.status in {"paired", "frozen"} for item in dwg_items),
        converting=sum(item.status == "converting" for item in dwg_items),
        failed=sum(item.status == "conversion_failed" for item in dwg_items),
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
        issues=issues,
        freeze_ready=batch.status in {"ready_to_freeze", "frozen"},
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )
