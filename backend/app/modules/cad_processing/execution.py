"""Shared attempt-aware primitives for CAD worker implementations.

Only behavior that is identical across conversion directions belongs here.
Version resolution, Stage invocation, error codes and result metadata stay in
their owning conversion package.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, get_storage_backend
from app.modules.jobs.interface import Job, JobStep, fail_job_attempt
from app.platform.storage.base import StorageObjectNotFound
from app.platform.time import business_now


class CadProcessingError(Exception):
    """Expected conversion failure whose message is safe for Job feedback."""


def exception_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message:
            return message
    message = str(exc)
    return message or exc.__class__.__name__


def mark_job_failed(
    db: Session,
    job_id: int,
    attempt: int,
    exc: Exception,
    *,
    error_code: str,
    logger: logging.Logger,
) -> None:
    """Commit failure only for the current Job attempt and contain DB errors."""
    try:
        fail_job_attempt(
            db,
            job_id,
            attempt=attempt,
            error_code=error_code,
            error_message=exception_message(exc),
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to mark CAD processing job %s as failed", job_id)


def resolve_source_file_id(job: Job) -> int | None:
    raw = (job.params_json or {}).get("file_id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return None


def stage_source_file(
    db: Session,
    source_file_id: int,
    work_dir: Path,
    *,
    fallback_extension: str,
) -> Path | None:
    """Resolve Local bytes or stream a MinIO object into the attempt workspace."""
    stored = db.get(StoredFile, source_file_id)
    if stored is None or stored.status == "deleted":
        return None

    storage = get_storage_backend()
    local_path = storage.local_path(stored.bucket, stored.storage_key)
    if local_path is not None:
        if not local_path.exists() or not local_path.is_file():
            raise StorageObjectNotFound(f"{stored.bucket}/{stored.storage_key}")
        return local_path

    destination = work_dir / f"source{stored.file_ext or fallback_extension}"
    try:
        with destination.open("wb") as output:
            for chunk in storage.iter_file(stored.bucket, stored.storage_key):
                output.write(chunk)
    except StorageObjectNotFound:
        return None
    return destination


def add_job_step(
    db: Session,
    job_id: int,
    attempt: int,
    step_name: str,
    worker_name: str,
    status: str,
    *,
    input_json: dict | None = None,
    output_json: dict | None = None,
    error_message: str | None = None,
    started_at: datetime | None = None,
) -> None:
    db.add(
        JobStep(
            job_id=job_id,
            attempt=attempt,
            step_name=step_name,
            worker_name=worker_name,
            status=status,
            input_json=input_json,
            output_json=output_json,
            error_message=error_message,
            started_at=started_at,
            finished_at=business_now(),
        )
    )


__all__ = [
    "CadProcessingError",
    "add_job_step",
    "exception_message",
    "mark_job_failed",
    "resolve_source_file_id",
    "stage_source_file",
]
