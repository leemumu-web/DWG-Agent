"""Resolve and stage the registered DXF members of one production batch."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.interface import (
    StoredFile,
    get_storage_backend,
    sanitize_filename,
)
from app.modules.jobs.interface import Job
from app.platform.storage.base import StorageObjectNotFound

logger = logging.getLogger(__name__)


def resolve_batch_name(job: Job) -> str | None:
    """Read and normalize ``params.batch_name`` from a Job."""
    params = job.params_json or {}
    raw = params.get("batch_name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def batch_workbook_stem(batch_name: str) -> str:
    """Return the filesystem-safe workbook stem for a registered batch."""
    return sanitize_filename(batch_name)


def stage_dxf_batch(
    db: Session,
    batch_name: str,
    work_dir: Path,
) -> tuple[list[Path], dict[str, Any]]:
    """Download every live registered DXF in a batch into ``work_dir``."""
    dxf_files = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.batch_name == batch_name,
                StoredFile.file_ext == ".dxf",
                StoredFile.status != "deleted",
            )
        ).all()
    )
    stats: dict[str, Any] = {
        "dxf_count": len(dxf_files),
        "downloaded": 0,
        "total_bytes": 0,
        "errors": [],
    }
    if not dxf_files:
        return [], stats

    storage = get_storage_backend()
    local_paths: list[Path] = []
    for stored_file in dxf_files:
        try:
            local = storage.local_path(stored_file.bucket, stored_file.storage_key)
            destination = work_dir / sanitize_filename(stored_file.original_name)
            if local is not None:
                if not local.exists() or not local.is_file():
                    raise StorageObjectNotFound(f"{stored_file.bucket}/{stored_file.storage_key}")
                destination.write_bytes(local.read_bytes())
            else:
                with destination.open("wb") as output:
                    for chunk in storage.iter_file(stored_file.bucket, stored_file.storage_key):
                        output.write(chunk)
            local_paths.append(destination)
            stats["downloaded"] += 1
            stats["total_bytes"] += destination.stat().st_size
        except Exception as exc:
            logger.warning(
                "Failed to stage DXF %s (file_id=%s): %s",
                stored_file.original_name,
                stored_file.id,
                exc,
            )
            stats["errors"].append(
                {
                    "file_id": stored_file.id,
                    "original_name": stored_file.original_name,
                    "error": str(exc),
                }
            )
    return local_paths, stats
