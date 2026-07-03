from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    get_db,
    get_project_membership,
    has_global_project_access,
)
from app.core.config import settings
from app.core.exceptions import AppHTTPException, forbidden, not_found
from app.models.drawing import Drawing, DrawingVersion
from app.models.file import StoredFile
from app.models.job import Job
from app.models.project import Project
from app.models.result import AnalysisResult
from app.schemas.common import ok, page_from_list
from app.schemas.file_schema import DownloadUrlRead, FileRead
from app.services.audit_service import write_audit_log
from app.services.storage_service import get_local_file_path, save_upload_file

router = APIRouter()
DOWNLOAD_URL_TTL_SECONDS = 300


def _download_signature(file_id: int, expires: int) -> str:
    payload = f"{file_id}:{expires}".encode()
    secret = settings.jwt_secret_key.encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def _build_signed_download_url(file_id: int) -> DownloadUrlRead:
    expires = int(time.time()) + DOWNLOAD_URL_TTL_SECONDS
    signature = _download_signature(file_id, expires)
    return DownloadUrlRead(
        url=f"/api/v1/files/{file_id}/download?expires={expires}&signature={signature}",
        expires_in=DOWNLOAD_URL_TTL_SECONDS,
    )


def _validate_download_signature(file_id: int, expires: int, signature: str) -> None:
    if expires < int(time.time()):
        raise AppHTTPException(403, "DOWNLOAD_URL_EXPIRED", "Download URL has expired.")
    expected = _download_signature(file_id, expires)
    if not hmac.compare_digest(expected, signature):
        raise AppHTTPException(
            403, "INVALID_DOWNLOAD_SIGNATURE", "Download URL signature is invalid."
        )


def _file_project_ids(db: Session, file_id: int) -> set[int]:
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


def _can_read_file(db: Session, current_user: CurrentUser, stored: StoredFile) -> bool:
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return True
    return any(
        get_project_membership(db, current_user, project_id)
        for project_id in _file_project_ids(db, stored.id)
    )


def _require_file_read_access(
    db: Session, current_user: CurrentUser, stored: StoredFile
) -> None:
    if not _can_read_file(db, current_user, stored):
        raise forbidden("File access is restricted.")


def _require_file_delete_access(current_user: CurrentUser, stored: StoredFile) -> None:
    if has_global_project_access(current_user) or stored.uploaded_by == current_user.id:
        return
    raise forbidden("Only the uploader or an administrator can delete this file.")


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    current_user: CurrentUser,
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    stored = await save_upload_file(db, upload, uploaded_by=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.upload",
        resource_type="file",
        resource_id=stored.id,
        after_json={"original_name": stored.original_name, "sha256": stored.sha256},
    )
    db.commit()
    return ok(FileRead.model_validate(stored), request.state.request_id)


@router.get("")
def list_files(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    files = list(
        db.scalars(
            select(StoredFile).where(StoredFile.status != "deleted").order_by(StoredFile.id.desc())
        ).all()
    )
    if not has_global_project_access(current_user):
        files = [stored for stored in files if _can_read_file(db, current_user, stored)]
    return page_from_list(
        [FileRead.model_validate(f) for f in files], page, page_size, request.state.request_id
    )


@router.get("/{file_id}")
def get_file(
    file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_read_access(db, current_user, stored)
    return ok(FileRead.model_validate(stored), request.state.request_id)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_delete_access(current_user, stored)
    stored.status = "deleted"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.delete",
        resource_type="file",
        resource_id=stored.id,
    )
    db.commit()
    return None


@router.get("/{file_id}/download-url")
def get_download_url(
    file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_read_access(db, current_user, stored)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.download_url",
        resource_type="file",
        resource_id=stored.id,
    )
    db.commit()
    return ok(_build_signed_download_url(file_id), request.state.request_id)


@router.get("/{file_id}/download")
def download_file(
    file_id: int,
    request: Request,
    current_user: CurrentUser,
    expires: int | None = Query(default=None),
    signature: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    _require_file_read_access(db, current_user, stored)
    if expires is None or signature is None:
        raise AppHTTPException(
            403, "INVALID_DOWNLOAD_SIGNATURE", "Download URL signature is required."
        )
    _validate_download_signature(file_id, expires, signature)
    path = get_local_file_path(stored)
    if not path.exists() or not path.is_file():
        raise not_found("StoredFileObject")
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.download",
        resource_type="file",
        resource_id=stored.id,
    )
    db.commit()
    return FileResponse(
        path,
        media_type=stored.content_type or "application/octet-stream",
        filename=stored.original_name,
    )
