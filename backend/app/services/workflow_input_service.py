from __future__ import annotations

import hashlib
import re
import unicodedata
from io import BytesIO
from pathlib import Path

import openpyxl
import xlrd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppHTTPException
from app.models.file import StoredFile
from app.models.workflow import WorkflowRun
from app.models.workflow_input import WorkflowInputBatch, WorkflowInputItem
from app.services.storage_service import (
    MIN_DWG_SIZE_BYTES,
    get_storage_backend,
    validate_dwg_header,
)
from app.storage.base import StorageError, StorageObjectNotFound

_WHITESPACE = re.compile(r"\s+")


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
