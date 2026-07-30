"""Production input registration, object verification and mutation locking."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import NoReturn

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.excel_processing.interface import (
    ExcelFinalInputError,
    ExcelFinalProcessError,
    ExcelFinalUnavailableError,
    ExcelStage1Inspection,
    inspect_excel_stage1_bytes,
)
from app.modules.files.interface import (
    MIN_DWG_SIZE_BYTES,
    StoredFile,
    get_storage_backend,
    validate_dwg_header,
)
from app.modules.jobs.interface import Job, cancel_job
from app.modules.workflows.models import WorkflowInputBatch, WorkflowInputItem, WorkflowRun
from app.platform.config.constants import EXCEL_FILE_EXTENSIONS
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage.base import StorageError, StorageObjectNotFound

_WHITESPACE = re.compile(r"\s+")
# One production intake is bounded at five thousand source drawings.  This is
# the authoritative server-side gate; browsers may give an earlier warning but
# must never be trusted to enforce the limit.
MAX_INPUT_DWG_FILES = 5000
ACTIVE_INPUT_JOB_STATUSES = {
    "pending",
    "queued",
    "running",
    "validating",
    "waiting_cad_worker",
}


@dataclass(frozen=True)
class FrozenInputReference:
    """Immutable deletion-guard projection without leaking workflow ORM rows."""

    workflow_id: int
    input_batch_id: int


@dataclass(frozen=True)
class InputRegistrationOutcome:
    """A durable input row plus an optional operator-facing validation failure."""

    item: WorkflowInputItem
    failure: dict[str, object] | None = None


def validate_input_excel_name(upload_name: str) -> None:
    """Accept the sole single-file production input format."""
    if Path(upload_name).suffix.lower() not in {".xls", ".xlsx"}:
        raise AppHTTPException(
            422,
            "INPUT_EXCEL_FILE_TYPE_NOT_ALLOWED",
            "The production Excel input must be one .xls or .xlsx file.",
        )


def validate_input_dwg_folder_manifest(
    upload_names: list[str],
    relative_paths: list[str],
) -> str:
    """Validate one browser-selected DWG folder before storing any object."""
    if not upload_names or len(upload_names) != len(relative_paths):
        raise AppHTTPException(
            422,
            "INPUT_FOLDER_MANIFEST_INVALID",
            "The folder manifest must describe every uploaded file exactly once.",
        )
    if len(upload_names) > MAX_INPUT_DWG_FILES:
        raise AppHTTPException(
            413,
            "INPUT_FOLDER_TOO_MANY_FILES",
            f"The selected folder may contain at most {MAX_INPUT_DWG_FILES} uploaded files.",
            {
                "maximum_files": MAX_INPUT_DWG_FILES,
                "selected_files": len(upload_names),
            },
        )

    roots: set[str] = set()
    normalized_paths: set[str] = set()
    extensions: list[str] = []
    dwg_stems: set[str] = set()
    for upload_name, raw_path in zip(upload_names, relative_paths, strict=True):
        slash_path = raw_path.replace("\\", "/")
        raw_parts = slash_path.split("/")
        path = PurePosixPath(slash_path)
        if (
            any(ord(character) < 32 or ord(character) == 127 for character in slash_path)
            or any(part in {"", ".", ".."} for part in raw_parts)
            or (raw_parts and re.match(r"^[A-Za-z]:", raw_parts[0]) is not None)
            or path.is_absolute()
            or len(path.parts) < 2
            or path.as_posix() != slash_path
            or unicodedata.normalize("NFC", upload_name)
            != unicodedata.normalize("NFC", path.name)
        ):
            raise AppHTTPException(
                422,
                "INPUT_FOLDER_MANIFEST_INVALID",
                "Every upload must be a regular file inside one selected folder.",
                {"path": raw_path},
            )
        key = unicodedata.normalize("NFC", slash_path).casefold()
        if key in normalized_paths:
            raise AppHTTPException(
                422,
                "INPUT_FOLDER_DUPLICATE_PATH",
                "The selected folder contains duplicate normalized paths.",
                {"path": raw_path},
            )
        normalized_paths.add(key)
        roots.add(unicodedata.normalize("NFC", path.parts[0]).casefold())
        extension = Path(path.name).suffix.lower()
        extensions.append(extension)
        if extension == ".dwg":
            stem = normalize_input_stem(path.name)
            if stem in dwg_stems:
                raise AppHTTPException(
                    422,
                    "INPUT_FOLDER_DUPLICATE_DRAWING_NAME",
                    "DWG filenames must be unique across the selected folder.",
                    {"file_name": path.name},
                )
            dwg_stems.add(stem)

    if len(roots) != 1:
        raise AppHTTPException(
            422,
            "INPUT_FOLDER_ROOT_MISMATCH",
            "All uploaded files must come from the same selected folder.",
        )
    if any(extension != ".dwg" for extension in extensions):
        raise AppHTTPException(
            422,
            "INPUT_DWG_FOLDER_FILE_TYPE_NOT_ALLOWED",
            "The DWG folder upload may contain only DWG files.",
            {"extensions": sorted(set(extensions) - {".dwg"})},
        )
    if extensions.count(".dwg") < 1:
        raise AppHTTPException(
            422,
            "INPUT_FOLDER_DWG_REQUIRED",
            "The selected folder must contain at least one DWG file.",
        )
    return PurePosixPath(relative_paths[0].replace("\\", "/")).parts[0]


def classify_human_input_extension(file_ext: str) -> str:
    """Map the only accepted human input extensions to immutable ledger roles."""
    normalized = file_ext.lower()
    if normalized == ".dxf":
        raise AppHTTPException(
            422,
            "INPUT_DXF_NOT_ALLOWED",
            "DXF must be generated by the server from a registered DWG input.",
        )
    if normalized == ".dwg":
        return "source_dwg"
    if normalized in EXCEL_FILE_EXTENSIONS:
        return "source_excel"
    raise AppHTTPException(
        422,
        "INPUT_FILE_TYPE_NOT_ALLOWED",
        "Production input accepts DWG files and exactly one Excel file.",
    )


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
        select(WorkflowInputBatch).where(WorkflowInputBatch.workflow_run_id == workflow.id)
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


def lock_input_batch(db: Session, batch: WorkflowInputBatch) -> WorkflowInputBatch:
    """Serialize mutable input operations and refresh their item collection."""
    locked = db.scalar(
        select(WorkflowInputBatch).where(WorkflowInputBatch.id == batch.id).with_for_update()
    )
    if locked is None:
        raise AppHTTPException(404, "INPUT_BATCH_NOT_FOUND", "Input batch not found.")
    db.expire(locked, ["items"])
    return locked


def read_verified_input_object(stored: StoredFile) -> bytes:
    """Read one registered object while rechecking its immutable SQL ledger."""
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


def validate_dwg_payload(payload: bytes) -> None:
    validate_dwg_header(payload[:6])
    if len(payload) < MIN_DWG_SIZE_BYTES:
        raise AppHTTPException(
            415,
            "FILE_NOT_DWG",
            f"File too small ({len(payload)} bytes) — legitimate DWG files exceed {MIN_DWG_SIZE_BYTES} bytes.",
        )


def inspect_excel_payload(
    *,
    file_name: str,
    payload: bytes,
    expected_sha256: str | None = None,
) -> ExcelStage1Inspection:
    """Use the Excel domain's canonical contract and map only operational failures."""
    try:
        return inspect_excel_stage1_bytes(
            file_name=file_name,
            payload=payload,
            expected_sha256=expected_sha256,
        )
    except ExcelFinalInputError:
        raise
    except ExcelFinalUnavailableError as exc:
        raise AppHTTPException(
            503,
            "EXCEL_STAGE1_UNAVAILABLE",
            "Excel 第一阶段检查服务当前不可用。",
            {"action": "请稍后重试；如果问题持续，请联系管理员检查服务状态。"},
        ) from exc
    except ExcelFinalProcessError as exc:
        raise AppHTTPException(
            503,
            "EXCEL_STAGE1_INTERNAL_ERROR",
            "Excel 第一阶段检查未能完成。",
            {"action": "请稍后重试；如果问题持续，请联系管理员并提供请求编号。"},
        ) from exc


def persisted_excel_failure(item: WorkflowInputItem) -> dict[str, object] | None:
    validation = item.validation_json
    if not isinstance(validation, dict):
        return None
    failure = validation.get("failure")
    return failure if isinstance(failure, dict) else None


def excel_validation_required_failure(
    item: WorkflowInputItem,
    *,
    message: str = "Excel 输入缺少可核验的登记记录。",
    action: str = "请从输入批次中移除该 Excel，并重新上传、登记。",
) -> dict[str, object]:
    return {
        "code": "EXCEL_INPUT_VALIDATION_REQUIRED",
        "message": message,
        "action": action,
        "contract_version": item.validation_contract_version or 1,
        "issues": [],
        "sheets": [],
        "meta": {
            "item_id": item.id,
            "issue_count": 0,
            "issues_truncated": False,
            "sheet_count": 0,
            "sheets_truncated": False,
        },
    }


def raise_excel_failure(failure: dict[str, object]) -> NoReturn:
    code = str(failure.get("code") or "EXCEL_INPUT_INVALID")
    message = str(failure.get("message") or "Excel 输入未通过检查。")
    raise AppHTTPException(
        409 if code == "EXCEL_INPUT_OBJECT_CHANGED" else 422,
        code,
        message,
        {"failure": failure},
    )


def register_input_file(
    db: Session,
    batch: WorkflowInputBatch,
    stored: StoredFile,
) -> InputRegistrationOutcome:
    batch = lock_input_batch(db, batch)
    if batch.status == "frozen":
        raise AppHTTPException(
            409, "INPUT_BATCH_FROZEN", "Frozen input batches cannot be modified."
        )
    existing = next((item for item in batch.items if item.file_id == stored.id), None)
    if existing is not None:
        return InputRegistrationOutcome(
            item=existing,
            failure=persisted_excel_failure(existing),
        )
    file_ext = stored.file_ext.lower()
    role = classify_human_input_extension(file_ext)
    if role == "source_excel" and any(item.role == "source_excel" for item in batch.items):
        raise AppHTTPException(
            409,
            "INPUT_EXCEL_ALREADY_EXISTS",
            "The input batch already contains an Excel file. Remove it before replacement.",
        )
    payload = read_verified_input_object(stored)
    if role == "source_dwg":
        validate_dwg_payload(payload)
        item = WorkflowInputItem(
            batch=batch,
            file_id=stored.id,
            role=role,
            original_name=stored.original_name,
            normalized_stem=normalize_input_stem(stored.original_name),
            status="uploaded",
        )
        failure = None
    else:
        try:
            inspection = inspect_excel_payload(
                file_name=stored.original_name,
                payload=payload,
                expected_sha256=stored.sha256,
            )
        except ExcelFinalInputError as exc:
            failure = exc.failure.as_dict()
            item = WorkflowInputItem(
                batch=batch,
                file_id=stored.id,
                role=role,
                original_name=stored.original_name,
                normalized_stem=normalize_input_stem(stored.original_name),
                status="failed",
                error_code=exc.failure.code,
                error_message=exc.failure.message,
                validation_json={"failure": failure},
                validation_contract_version=exc.failure.contract_version,
                validated_sha256=stored.sha256,
            )
        else:
            failure = None
            inspection_payload = asdict(inspection)
            inspection_payload["warnings"] = list(inspection.warnings)
            inspection_payload["ignored_sheets"] = list(inspection.ignored_sheets)
            item = WorkflowInputItem(
                batch=batch,
                file_id=stored.id,
                role=role,
                original_name=stored.original_name,
                normalized_stem=normalize_input_stem(stored.original_name),
                status="uploaded",
                validation_json={"inspection": inspection_payload},
                validation_contract_version=inspection.input_contract_version,
                validated_sha256=stored.sha256,
            )
    db.add(item)
    db.flush()
    return InputRegistrationOutcome(item=item, failure=failure)


def get_input_batch(db: Session, workflow_id: int) -> WorkflowInputBatch:
    batch = db.scalar(
        select(WorkflowInputBatch).where(WorkflowInputBatch.workflow_run_id == workflow_id)
    )
    if batch is None:
        raise AppHTTPException(404, "INPUT_BATCH_NOT_FOUND", "Input batch not found.")
    return batch


def remove_input_item(
    db: Session,
    batch: WorkflowInputBatch,
    item_id: int,
) -> None:
    batch = lock_input_batch(db, batch)
    if batch.status == "frozen":
        raise AppHTTPException(
            409, "INPUT_BATCH_FROZEN", "Frozen input batches cannot be modified."
        )
    item = next((value for value in batch.items if value.id == item_id), None)
    if item is None:
        raise AppHTTPException(404, "INPUT_ITEM_NOT_FOUND", "Input item not found.")
    if item.conversion_job_id is not None:
        job = db.get(Job, item.conversion_job_id)
        if job is not None and job.status in ACTIVE_INPUT_JOB_STATUSES:
            cancel_job(db, job)
    db.delete(item)
    db.flush()
    batch.status = "uploading"
    batch.error_code = None
    batch.error_message = None


def find_frozen_input_reference(
    db: Session,
    file_id: int,
    *,
    for_update: bool = False,
) -> FrozenInputReference | None:
    """Return only the frozen-manifest identifiers needed by file deletion guards."""
    stmt = (
        select(WorkflowInputBatch)
        .join(
            WorkflowInputItem,
            WorkflowInputItem.input_batch_id == WorkflowInputBatch.id,
        )
        .where(
            WorkflowInputBatch.status == "frozen",
            or_(
                WorkflowInputItem.file_id == file_id,
                WorkflowInputItem.derived_dxf_file_id == file_id,
            ),
        )
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    batch = db.scalar(stmt)
    if batch is None:
        return None
    return FrozenInputReference(
        workflow_id=batch.workflow_run_id,
        input_batch_id=batch.id,
    )
