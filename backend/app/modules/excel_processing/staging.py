"""Source-file resolution and download for Excel Final."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.modules.files.interface import (
    StoredFile,
    get_storage_backend,
    sanitize_filename,
)
from app.modules.jobs.interface import Job
from app.platform.config.constants import EXCEL_FILE_EXTENSIONS
from app.platform.storage.base import StorageObjectNotFound


def resolve_file_id(job: Job) -> int | None:
    """Read a positive file id from a Job's persisted parameters."""
    raw = (job.params_json or {}).get("file_id")
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def stage_excel_source(
    db: Session,
    file_id: int,
    work_dir: Path,
) -> tuple[Path, StoredFile]:
    """Download one registered source object into the attempt work directory."""
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise FileNotFoundError(f"File {file_id} not found or deleted")
    if stored.file_ext and stored.file_ext.lower() not in EXCEL_FILE_EXTENSIONS:
        raise ValueError(f"File {file_id} is not an Excel file (ext={stored.file_ext})")

    storage = get_storage_backend()
    destination = work_dir / sanitize_filename(stored.original_name)
    local = storage.local_path(stored.bucket, stored.storage_key)
    if local is not None:
        if not local.exists() or not local.is_file():
            raise StorageObjectNotFound(f"{stored.bucket}/{stored.storage_key}")
        destination.write_bytes(local.read_bytes())
    else:
        with destination.open("wb") as output:
            for chunk in storage.iter_file(stored.bucket, stored.storage_key):
                output.write(chunk)
    return destination, stored


__all__ = ["resolve_file_id", "stage_excel_source"]
