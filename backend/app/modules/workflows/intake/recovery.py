"""Exact, audit-backed restoration of a cleared production input batch."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile
from app.modules.operations.audit.interface import latest_audit_log
from app.modules.workflows.intake.registration import register_input_file
from app.modules.workflows.models import WorkflowInputBatch
from app.platform.http.exceptions import AppHTTPException

_CLEAR_ACTION = "workflow_input_folders.clear"
_RESTORE_ACTION = "workflow_input_folders.restore"


def recoverable_input_file_ids(db: Session, batch: WorkflowInputBatch) -> list[int]:
    """Return the exact latest cleared sources unless that clear was restored."""
    event = latest_audit_log(
        db,
        actions={_CLEAR_ACTION, _RESTORE_ACTION},
        resource_type="workflow_input_batch",
        resource_id=batch.id,
    )
    if event is None or event.action != _CLEAR_ACTION:
        return []
    payload = event.after_json if isinstance(event.after_json, dict) else {}
    raw_ids = payload.get("removed_file_ids")
    if not isinstance(raw_ids, list):
        return []
    file_ids = [value for value in raw_ids if isinstance(value, int) and value > 0]
    return file_ids if len(file_ids) == len(raw_ids) and len(set(file_ids)) == len(file_ids) else []


def restore_cleared_input_files(
    db: Session,
    batch: WorkflowInputBatch,
) -> list[int]:
    """Re-register only source objects named in the latest clear audit event."""
    if batch.items:
        raise AppHTTPException(
            409,
            "INPUT_RESTORE_NOT_AVAILABLE",
            "The current input batch is not empty and cannot restore a previous clear.",
        )
    file_ids = recoverable_input_file_ids(db, batch)
    if not file_ids:
        raise AppHTTPException(
            409,
            "INPUT_RESTORE_NOT_AVAILABLE",
            "No exact cleared input record is available for restoration.",
        )
    expected_batch_name = f"workflow-input-{batch.id}"
    for file_id in file_ids:
        stored = db.get(StoredFile, file_id)
        if (
            stored is None
            or stored.status != "available"
            or stored.batch_name != expected_batch_name
        ):
            raise AppHTTPException(
                409,
                "INPUT_RESTORE_FILE_UNAVAILABLE",
                "A cleared source file is no longer available for exact restoration.",
                {"file_id": file_id},
            )
        outcome = register_input_file(db, batch, stored)
        if outcome.failure is not None:
            from app.modules.workflows.intake.registration import raise_excel_failure

            raise_excel_failure(outcome.failure)
    batch.status = "uploading"
    batch.error_code = None
    batch.error_message = None
    return file_ids


__all__ = ["recoverable_input_file_ids", "restore_cleared_input_files"]
