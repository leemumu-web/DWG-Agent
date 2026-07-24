"""Immutable input manifest creation and source-intake completion."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.modules.excel_processing.interface import ExcelFinalInputError
from app.modules.files.interface import StoredFile
from app.modules.projects.interface import Drawing, DrawingVersion
from app.modules.workflows.artifacts import attach_artifact
from app.modules.workflows.intake import conversion, registration
from app.modules.workflows.lifecycle import complete_manual_stage
from app.modules.workflows.models import WorkflowInputBatch, WorkflowInputItem
from app.platform.http.exceptions import AppHTTPException

_WHITESPACE = re.compile(r"\s+")


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


def _stored_excel_failure(item: WorkflowInputItem) -> dict[str, object] | None:
    if not isinstance(item.validation_json, dict):
        return None
    failure = item.validation_json.get("failure")
    return failure if isinstance(failure, dict) else None


def _validation_required_failure(item: WorkflowInputItem) -> dict[str, object]:
    return {
        "code": "EXCEL_INPUT_VALIDATION_REQUIRED",
        "message": "Excel 输入缺少可核验的登记记录。",
        "action": "请从输入批次中移除该 Excel，并重新上传、登记。",
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


def _raise_excel_failure(failure: dict[str, object]) -> None:
    code = str(failure.get("code") or "EXCEL_INPUT_INVALID")
    message = str(failure.get("message") or "Excel 输入未通过检查。")
    status_code = 409 if code == "EXCEL_INPUT_OBJECT_CHANGED" else 422
    raise AppHTTPException(
        status_code,
        code,
        message,
        {"failure": failure},
    )


def freeze_input_batch(
    db: Session,
    batch: WorkflowInputBatch,
) -> WorkflowInputBatch:
    batch = registration.lock_input_batch(db, batch)
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
        raise AppHTTPException(409, "INPUT_DWG_REQUIRED", "At least one DWG input is required.")
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
            {"conflicts": [[item.original_name for item in conflict] for conflict in conflicts]},
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
        if item.role == "source_excel":
            persisted_failure = _stored_excel_failure(item)
            if persisted_failure is not None:
                _raise_excel_failure(persisted_failure)
            if (
                item.validation_contract_version is None
                or item.validated_sha256 is None
                or not isinstance(item.validation_json, dict)
                or not isinstance(item.validation_json.get("inspection"), dict)
            ):
                _raise_excel_failure(_validation_required_failure(item))
        payload = registration.read_verified_input_object(stored)
        if item.role == "source_dwg":
            registration.validate_dwg_payload(payload)
        else:
            try:
                inspection = registration.inspect_excel_payload(
                    file_name=stored.original_name,
                    payload=payload,
                    expected_sha256=item.validated_sha256,
                )
            except ExcelFinalInputError as exc:
                _raise_excel_failure(exc.failure.as_dict())
            if inspection.input_contract_version != item.validation_contract_version:
                _raise_excel_failure(_validation_required_failure(item))

    conversion.sync_input_batch(db, batch)
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
        display_stem = _display_input_stem(item.original_name)
        drawing = Drawing(
            project_id=batch.project_id,
            drawing_no=display_stem,
            title=display_stem,
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
        attach_artifact(
            db,
            workflow,
            stage_code="source_intake",
            artifact_type="source_file",
            file_id=source.id,
            metadata={"batch_id": batch.id, "drawing_id": drawing.id},
        )
        attach_artifact(
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
    attach_artifact(
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
    complete_manual_stage(workflow, "source_intake")
    db.flush()
    return batch
