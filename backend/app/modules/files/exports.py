from __future__ import annotations

import hashlib
import hmac
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.result import AnalysisResult
from app.modules.files.models import StoredFile
from app.modules.files.schemas import (
    DownloadUrlRead,
    ZipAvailabilityPreview,
    ZipFormatAvailability,
)
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException
from app.platform.storage import factory as storage_factory

DOWNLOAD_URL_TTL_SECONDS = 300


@dataclass(frozen=True)
class PreparedExport:
    path: Path
    filename: str
    size_bytes: int
    included_file_ids: tuple[int, ...]


@dataclass(frozen=True)
class ZipAvailabilityResolution:
    requested_ids: tuple[int, ...]
    requested_formats: tuple[str, ...]
    source_files: dict[int, StoredFile]
    selected_files: dict[tuple[int, str], StoredFile]
    missing_by_format: dict[str, tuple[int, ...]]


def download_signature(file_id: int, expires: int) -> str:
    payload = f"{file_id}:{expires}".encode()
    secret = settings.jwt_secret_key.encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def build_signed_download_url(file_id: int) -> DownloadUrlRead:
    expires = int(time.time()) + DOWNLOAD_URL_TTL_SECONDS
    signature = download_signature(file_id, expires)
    return DownloadUrlRead(
        url=f"/api/v1/files/{file_id}/download?expires={expires}&signature={signature}",
        expires_in=DOWNLOAD_URL_TTL_SECONDS,
    )


def validate_download_signature(file_id: int, expires: int, signature: str) -> None:
    if expires < int(time.time()):
        raise AppHTTPException(403, "DOWNLOAD_URL_EXPIRED", "Download URL has expired.")
    expected = download_signature(file_id, expires)
    if not hmac.compare_digest(expected, signature):
        raise AppHTTPException(
            403, "INVALID_DOWNLOAD_SIGNATURE", "Download URL signature is invalid."
        )


def download_headers(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}


def build_result_map(db: Session, file_ids: list[int]) -> dict[int, StoredFile | None]:
    """For each source file_id, find the conversion result file (if any).

    Works for both DWG→DXF and DXF→DWG pipelines generically:
    - Source .dwg files → look for TASK_DWG_TO_DXF results (→ .dxf)
    - Source .dxf files → look for TASK_DXF_TO_DWG results (→ .dwg)

    Optimisation: O(n+m) single-scan build instead of nested loops.
    """
    from app.platform.config.constants import TASK_DWG_TO_DXF, TASK_DXF_TO_DWG

    file_ids_set = frozenset(file_ids)
    if not file_ids_set:
        return {}

    file_id_to_job_id: dict[int, int] = {}
    for j in db.scalars(
        select(Job).where(
            Job.task_type.in_([TASK_DWG_TO_DXF, TASK_DXF_TO_DWG]),
            Job.status == "succeeded",
            Job.params_json["file_id"].as_integer().in_(file_ids_set),
        ).order_by(Job.id.desc())
    ).all():
        fid = (j.params_json or {}).get("file_id") if isinstance(j.params_json, dict) else None
        if isinstance(fid, int) and fid in file_ids_set and fid not in file_id_to_job_id:
            file_id_to_job_id[fid] = j.id

    if not file_id_to_job_id:
        return {fid: None for fid in file_ids}

    job_ids = list(file_id_to_job_id.values())
    job_id_to_result: dict[int, AnalysisResult] = {}
    result_file_ids: list[int] = []
    for r in db.scalars(
        select(AnalysisResult).where(
            AnalysisResult.job_id.in_(job_ids),
            AnalysisResult.result_file_id.is_not(None),
            AnalysisResult.status == "succeeded",
        ).order_by(AnalysisResult.id.desc())
    ).all():
        if (
            r.job_id is not None
            and r.result_file_id is not None
            and r.job_id not in job_id_to_result
        ):
            job_id_to_result[r.job_id] = r
            result_file_ids.append(r.result_file_id)

    if not result_file_ids:
        return {fid: None for fid in file_ids}

    result_files: dict[int, StoredFile] = {}
    for f in db.scalars(
        select(StoredFile).where(
            StoredFile.id.in_(result_file_ids),
            StoredFile.status != "deleted",
        )
    ).all():
        result_files[f.id] = f

    out: dict[int, StoredFile | None] = {fid: None for fid in file_ids}
    for fid, job_id in file_id_to_job_id.items():
        result = job_id_to_result.get(job_id)
        if result and result.result_file_id:
            out[fid] = result_files.get(result.result_file_id)
    return out


# Legacy alias — kept for backward compatibility with existing callers.
build_dxf_result_map = build_result_map


def _resolve_zip_availability(
    db: Session,
    file_ids: list[int],
    formats: list[str],
) -> ZipAvailabilityResolution:
    requested_ids = tuple(dict.fromkeys(file_ids))
    if not requested_ids:
        raise AppHTTPException(422, "INVALID_PARAMS", "file_ids must not be empty.")
    requested_formats = tuple(dict.fromkeys(formats))
    if not requested_formats or any(item not in {"dwg", "dxf"} for item in requested_formats):
        raise AppHTTPException(
            422,
            "INVALID_PARAMS",
            "formats must contain only dwg or dxf.",
        )

    source_files = {
        stored.id: stored
        for stored in db.scalars(
            select(StoredFile).where(
                StoredFile.id.in_(requested_ids), StoredFile.status != "deleted"
            )
        ).all()
    }
    missing_ids = [file_id for file_id in requested_ids if file_id not in source_files]
    if missing_ids:
        raise AppHTTPException(
            404,
            "FILE_EXPORT_SOURCE_MISSING",
            "One or more requested export files are unavailable.",
            {"file_ids": missing_ids[:20], "missing_count": len(missing_ids)},
        )

    result_map = build_result_map(db, list(requested_ids))
    selected_files: dict[tuple[int, str], StoredFile] = {}
    missing_by_format: dict[str, tuple[int, ...]] = {}
    for requested_format in requested_formats:
        missing_for_format: list[int] = []
        for file_id in requested_ids:
            source = source_files[file_id]
            result = result_map.get(file_id)
            selected: StoredFile | None = None
            if source.file_ext == f".{requested_format}":
                selected = source
            elif result and result.file_ext == f".{requested_format}":
                selected = result
            if selected is None:
                missing_for_format.append(file_id)
            else:
                selected_files[(file_id, requested_format)] = selected
        missing_by_format[requested_format] = tuple(missing_for_format)

    return ZipAvailabilityResolution(
        requested_ids=requested_ids,
        requested_formats=requested_formats,
        source_files=source_files,
        selected_files=selected_files,
        missing_by_format=missing_by_format,
    )


def preview_zip_availability(
    db: Session,
    file_ids: list[int],
    formats: list[str],
) -> ZipAvailabilityPreview:
    resolution = _resolve_zip_availability(db, file_ids, formats)
    format_previews: list[ZipFormatAvailability] = []
    for requested_format in resolution.requested_formats:
        missing = resolution.missing_by_format[requested_format]
        format_previews.append(
            ZipFormatAvailability(
                format=requested_format,
                available_count=len(resolution.requested_ids) - len(missing),
                missing_count=len(missing),
                missing_file_ids=list(missing[:20]),
                complete=not missing,
            )
        )
    return ZipAvailabilityPreview(
        file_count=len(resolution.requested_ids),
        formats=format_previews,
        can_download=all(item.complete for item in format_previews),
    )


def build_zip(
    db: Session,
    file_ids: list[int],
    formats: list[str],
    folder_name: str,
) -> tuple[bytes, str]:
    """Compatibility wrapper returning bytes for callers not yet migrated to streaming."""
    prepared = build_zip_to_path(db, file_ids, formats, folder_name)
    try:
        return prepared.path.read_bytes(), prepared.filename
    finally:
        prepared.path.unlink(missing_ok=True)


def build_zip_to_path(
    db: Session,
    file_ids: list[int],
    formats: list[str],
    folder_name: str,
) -> PreparedExport:
    """Build a strict ZIP incrementally on disk.

    Every requested source and format must be available. Storage read failures
    abort the whole export instead of returning a plausible but incomplete ZIP.
    """

    resolution = _resolve_zip_availability(db, file_ids, formats)
    requested_ids = resolution.requested_ids
    requested_formats = resolution.requested_formats
    source_files = resolution.source_files

    def _stem(f: StoredFile) -> str:
        return f.original_name.rsplit(".", 1)[0] if "." in f.original_name else f.original_name

    # Two-pass dedup: count occurrences first, then assign suffixes
    stems: list[str] = [_stem(source_files[file_id]) for file_id in requested_ids]
    stem_count: dict[str, int] = {}
    for s in stems:
        stem_count[s] = stem_count.get(s, 0) + 1
    stem_seq: dict[str, int] = {}

    export_items: list[tuple[int, StoredFile, str]] = []
    for file_id in requested_ids:
        src = source_files[file_id]
        stem = _stem(src)
        seq = stem_seq.get(stem, 0) + 1
        stem_seq[stem] = seq
        disamb = f"({seq})" if stem_count.get(stem, 0) > 1 else ""
        for requested_format in requested_formats:
            selected = resolution.selected_files.get((file_id, requested_format))
            if selected is None:
                raise AppHTTPException(
                    409,
                    "FILE_EXPORT_FORMAT_UNAVAILABLE",
                    f"Requested {requested_format.upper()} format is not available.",
                    {"file_id": file_id, "format": requested_format},
                )
            export_items.append(
                (
                    file_id,
                    selected,
                    f"{folder_name}/{stem}{disamb}.{requested_format}",
                )
            )

    storage = storage_factory.get_storage_backend()
    tmp = NamedTemporaryFile(suffix=".zip", delete=False)
    path = Path(tmp.name)
    tmp.close()
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for file_id, stored, archive_name in export_items:
                try:
                    with archive.open(archive_name, "w", force_zip64=True) as target:
                        for chunk in storage.iter_file(stored.bucket, stored.storage_key):
                            target.write(chunk)
                except Exception as exc:
                    raise AppHTTPException(
                        409,
                        "STORAGE_INCONSISTENT",
                        "A required stored object could not be read for export.",
                        {"file_id": file_id},
                    ) from exc
        size_bytes = path.stat().st_size
    except BaseException:
        path.unlink(missing_ok=True)
        raise

    return PreparedExport(
        path=path,
        filename=f"{folder_name}.zip",
        size_bytes=size_bytes,
        included_file_ids=requested_ids,
    )
