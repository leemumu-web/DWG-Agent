from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import AppHTTPException, forbidden
from app.core.permissions import get_project_membership, has_global_project_access
from app.models.drawing import Drawing, DrawingVersion
from app.models.file import StoredFile
from app.models.job import Job
from app.models.project import Project
from app.models.result import AnalysisResult
from app.models.user import User
from app.schemas.file_schema import DownloadUrlRead

DOWNLOAD_URL_TTL_SECONDS = 300


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
    from app.core.constants import TASK_DWG_TO_DXF, TASK_DXF_TO_DWG

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


def build_zip(
    db: Session,
    file_ids: list[int],
    formats: list[str],
    folder_name: str,
) -> tuple[bytes, str]:
    """Build a zip archive containing requested format versions for the given
    source file ids.  Returns (zip_bytes, filename).

    Direction-agnostic:
    - "dwg" format → include DWG version (source if source is .dwg, or result if .dwg)
    - "dxf" format → include DXF version (source if source is .dxf, or result if .dxf)
    - Works for both /files/dwg2dxf and /files/dxf2dwg pages.
    """
    import io
    import zipfile

    from app.services.storage_service import get_storage_backend

    storage = get_storage_backend()
    want_dwg = "dwg" in formats
    want_dxf = "dxf" in formats

    # Load source files
    source_files: dict[int, StoredFile] = {}
    for f in db.scalars(
        select(StoredFile).where(
            StoredFile.id.in_(file_ids), StoredFile.status != "deleted"
        )
    ).all():
        source_files[f.id] = f

    # Load conversion results (either DWG→DXF or DXF→DWG)
    result_map = build_result_map(db, file_ids)

    def _stem(f: StoredFile) -> str:
        return f.original_name.rsplit(".", 1)[0] if "." in f.original_name else f.original_name

    # Two-pass dedup: count occurrences first, then assign suffixes
    stems: list[str] = [_stem(source_files[fid]) for fid in file_ids if fid in source_files]
    stem_count: dict[str, int] = {}
    for s in stems:
        stem_count[s] = stem_count.get(s, 0) + 1
    stem_seq: dict[str, int] = {}

    def _read(bucket: str, key: str) -> bytes | None:
        try:
            return b"".join(storage.iter_file(bucket, key))
        except Exception:
            return None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fid in file_ids:
            src = source_files.get(fid)
            if not src:
                continue
            stem = _stem(src)
            seq = stem_seq.get(stem, 0) + 1
            stem_seq[stem] = seq
            # Only disambiguate when the stem appears more than once
            disamb = f"({seq})" if stem_count.get(stem, 0) > 1 else ""

            result = result_map.get(fid)

            # DWG format: source if source is .dwg, or result if result is .dwg
            if want_dwg:
                dwg_file = None
                if src.file_ext == ".dwg":
                    dwg_file = src
                elif result and result.file_ext == ".dwg":
                    dwg_file = result
                if dwg_file:
                    dwg_bytes = _read(dwg_file.bucket, dwg_file.storage_key)
                    if dwg_bytes:
                        zf.writestr(f"{folder_name}/{stem}{disamb}.dwg", dwg_bytes)

            # DXF format: source if source is .dxf, or result if result is .dxf
            if want_dxf:
                dxf_file = None
                if src.file_ext == ".dxf":
                    dxf_file = src
                elif result and result.file_ext == ".dxf":
                    dxf_file = result
                if dxf_file:
                    dxf_bytes = _read(dxf_file.bucket, dxf_file.storage_key)
                    if dxf_bytes:
                        zf.writestr(f"{folder_name}/{stem}{disamb}.dxf", dxf_bytes)

    buf.seek(0)
    return buf.getvalue(), f"{folder_name}.zip"


def file_project_ids(db: Session, file_id: int) -> set[int]:
    drawing_project_ids = db.scalars(
        select(Drawing.project_id)
        .join(DrawingVersion, DrawingVersion.drawing_id == Drawing.id)
        .join(Project, Project.id == Drawing.project_id)
        .where(
            DrawingVersion.file_id == file_id,
            Drawing.status != "deleted",
            Project.status != "deleted",
        )
    ).all()
    result_project_ids = db.scalars(
        select(Job.project_id)
        .join(AnalysisResult, AnalysisResult.job_id == Job.id)
        .join(Project, Project.id == Job.project_id)
        .where(
            AnalysisResult.result_file_id == file_id,
            Job.project_id.is_not(None),
            Project.status != "deleted",
        )
    ).all()
    return {
        project_id
        for project_id in (*drawing_project_ids, *result_project_ids)
        if project_id is not None
    }


def can_read_file(db: Session, current_user: User, stored: StoredFile) -> bool:
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return True
    return any(
        get_project_membership(db, current_user, project_id)
        for project_id in file_project_ids(db, stored.id)
    )


def require_file_read_access(
    db: Session, current_user: User, stored: StoredFile
) -> None:
    if not can_read_file(db, current_user, stored):
        raise forbidden("File access is restricted.")


def require_file_delete_access(current_user: User, stored: StoredFile) -> None:
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return
    raise forbidden("Only the uploader or an administrator can delete this file.")
