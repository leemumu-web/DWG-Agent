from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.access import (
    file_list_access_filter,
    require_file_delete_access,
    require_file_read_access,
)
from app.modules.files.lifecycle import soft_delete_file_in_transaction
from app.modules.files.models import StoredFile
from app.modules.files.schemas import BulkDeleteRequest, FileRead
from app.modules.identity.interface import CurrentUser
from app.modules.operations.audit.interface import write_audit_log
from app.platform.config.validators import validate_sort_by
from app.platform.database.pagination import paginate_scalars
from app.platform.http.dependencies import get_db
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found

static_router = APIRouter()
item_router = APIRouter()

@static_router.get("")
def list_files(
    request: Request,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc", pattern=r"^(asc|desc)$"),
    batch_name: str = Query(""),
    file_ext: str = Query("", description="Filter by file extension, e.g. '.dwg' or '.dxf'"),
    db: Session = Depends(get_db),
):
    sort_column = validate_sort_by("files", sort_by)
    sort_dir_value = sort_dir.strip().lower()
    order_clause = getattr(StoredFile, sort_column)
    if sort_dir_value == "asc":
        order_clause = order_clause.asc()
    else:
        order_clause = order_clause.desc()
    stmt = select(StoredFile).where(StoredFile.status != "deleted")
    if batch_name.strip():
        stmt = stmt.where(StoredFile.batch_name == batch_name.strip())
    if file_ext.strip():
        stmt = stmt.where(StoredFile.file_ext == file_ext.strip())
    tie_breaker = StoredFile.id.asc() if sort_dir_value == "asc" else StoredFile.id.desc()
    stmt = stmt.where(file_list_access_filter(current_user)).order_by(order_clause, tie_breaker)
    files, total = paginate_scalars(db, stmt, page_no=page, page_size=page_size)
    return page_response(
        [FileRead.model_validate(f) for f in files],
        page,
        page_size,
        total,
        request.state.request_id,
    )


# ── batches ─────────────────────────────────────────────────────────────────
# NOTE: must be registered BEFORE /{file_id} to avoid route shadowing.

@item_router.get("/{file_id}")
def get_file(
    file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    require_file_read_access(db, current_user, stored)
    return ok(FileRead.model_validate(stored), request.state.request_id)


@item_router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    file_id: int, request: Request, current_user: CurrentUser, db: Session = Depends(get_db)
):
    stored = db.get(StoredFile, file_id)
    if not stored or stored.status == "deleted":
        raise not_found("File")
    require_file_delete_access(db, current_user, stored)
    soft_delete_file_in_transaction(
        db,
        stored,
        actor_user_id=current_user.id,
        request_id=request.state.request_id,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="files.delete",
        resource_type="file",
        resource_id=stored.id,
        request=request,
    )
    db.commit()
    return None

@static_router.post("/bulk-delete", status_code=status.HTTP_204_NO_CONTENT)
def bulk_delete_files(
    request: Request,
    payload: BulkDeleteRequest,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if not payload.file_ids:
        raise AppHTTPException(422, "INVALID_PARAMS", "file_ids must not be empty.")
    stored_list = list(
        db.scalars(
            select(StoredFile).where(
                StoredFile.id.in_(payload.file_ids), StoredFile.status != "deleted"
            )
        ).all()
    )
    for s in stored_list:
        require_file_delete_access(db, current_user, s)
        soft_delete_file_in_transaction(
            db,
            s,
            actor_user_id=current_user.id,
            request_id=request.state.request_id,
        )
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="files.bulk_delete",
            resource_type="file",
            resource_id=s.id,
            request=request,
        )
    db.commit()
    return None
