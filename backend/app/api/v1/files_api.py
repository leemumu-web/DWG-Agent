from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import CurrentUser, get_db
from backend.app.core.exceptions import not_found
from backend.app.models.file import StoredFile
from backend.app.schemas.common import ok, page
from backend.app.schemas.file_schema import DownloadUrlRead, FileRead
from backend.app.services.audit_service import write_audit_log
from backend.app.services.storage_service import get_local_file_path, save_upload_file

router = APIRouter()


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(request: Request, upload: UploadFile = File(...), db: Session = Depends(get_db), current_user: CurrentUser = None):
    stored = await save_upload_file(db, upload, uploaded_by=current_user.id)
    write_audit_log(db, actor_user_id=current_user.id, action="files.upload", resource_type="file", resource_id=stored.id, after_json={"original_name": stored.original_name, "sha256": stored.sha256})
    db.commit()
    return ok(FileRead.model_validate(stored), request.state.request_id)


@router.get("")
def list_files(request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    files = list(db.scalars(select(StoredFile).where(StoredFile.status != "deleted").order_by(StoredFile.id.desc())).all())
    return page([FileRead.model_validate(f) for f in files], 1, len(files), len(files), request.state.request_id)


@router.get("/{file_id}")
def get_file(file_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    return ok(FileRead.model_validate(stored), request.state.request_id)


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: int, db: Session = Depends(get_db), current_user: CurrentUser = None):
    stored = db.get(StoredFile, file_id)
    if not stored:
        raise not_found("File")
    stored.status = "deleted"
    write_audit_log(db, actor_user_id=current_user.id, action="files.delete", resource_type="file", resource_id=stored.id)
    db.commit()
    return None


@router.get("/{file_id}/download-url")
def get_download_url(file_id: int, request: Request, db: Session = Depends(get_db), current_user: CurrentUser = None):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    write_audit_log(db, actor_user_id=current_user.id, action="files.download_url", resource_type="file", resource_id=stored.id)
    db.commit()
    return ok(DownloadUrlRead(url=f"/api/v1/files/{file_id}/download", expires_in=300), request.state.request_id)


@router.get("/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db), current_user: CurrentUser = None):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    path = get_local_file_path(stored)
    if not path.exists() or not path.is_file():
        raise not_found("StoredFileObject")
    write_audit_log(db, actor_user_id=current_user.id, action="files.download", resource_type="file", resource_id=stored.id)
    db.commit()
    return FileResponse(
        path,
        media_type=stored.content_type or "application/octet-stream",
        filename=stored.original_name,
    )
