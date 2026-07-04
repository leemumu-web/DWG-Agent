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
