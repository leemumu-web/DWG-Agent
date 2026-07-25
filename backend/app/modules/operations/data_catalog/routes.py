from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

import app.modules.files.interface as storage_service
from app.modules.files.interface import FileRead, FileTransfer, StoredFile
from app.modules.identity.interface import get_current_user
from app.modules.operations.data_catalog.presentation import (
    storage_object_data,
    transfer_data,
)
from app.modules.operations.data_catalog.queries import (
    data_overview,
    query_data_files,
    query_transfers,
    registered_files_by_storage_key,
)
from app.platform.config.settings import settings
from app.platform.http.dependencies import DbSession
from app.platform.http.envelopes import ok
from app.platform.http.envelopes import page as page_response
from app.platform.http.exceptions import AppHTTPException, not_found
from app.platform.storage.base import StorageError

router = APIRouter()
data_reader = get_current_user


@router.get("/overview")
def get_data_overview(
    request: Request,
    db: DbSession,
    _current_user=Depends(data_reader),
):
    return ok(data_overview(db), request.state.request_id)


@router.get("/files")
def list_data_files(
    request: Request,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: str = Query("", max_length=255),
    status: str | None = Query(default=None),
    bucket: str | None = Query(default=None),
    file_ext: str | None = Query(default=None),
    _current_user=Depends(data_reader),
):
    rows, total = query_data_files(
        db,
        page=page,
        page_size=page_size,
        search=search,
        status=status,
        bucket=bucket,
        file_ext=file_ext,
    )
    return page_response(
        [FileRead.model_validate(row) for row in rows],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.get("/files/{file_id}")
def get_data_file(
    file_id: int,
    request: Request,
    db: DbSession,
    _current_user=Depends(data_reader),
):
    row = db.get(StoredFile, file_id)
    if row is None:
        raise not_found("StoredFile")
    return ok(FileRead.model_validate(row), request.state.request_id)


@router.get("/objects")
def list_storage_objects(
    request: Request,
    db: DbSession,
    bucket: str = Query(...),
    prefix: str = Query("", max_length=512),
    cursor: str | None = Query(default=None, max_length=512),
    page_size: int = Query(50, ge=1, le=200),
    _current_user=Depends(data_reader),
):
    if bucket not in settings.minio_bucket_names:
        raise AppHTTPException(422, "INVALID_BUCKET", "Bucket is not configured.")
    # Release the auth/RBAC transaction before potentially slow object listing.
    db.rollback()
    try:
        object_page = storage_service.get_storage_backend().list_objects(
            bucket,
            prefix=prefix,
            cursor=cursor,
            page_size=page_size,
        )
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "STORAGE_LIST_FAILED",
            "Storage objects could not be listed.",
        ) from exc

    registered = registered_files_by_storage_key(
        db,
        bucket=bucket,
        keys=[item.storage_key for item in object_page.items],
    )
    response = ok(
        [storage_object_data(item, registered) for item in object_page.items],
        request.state.request_id,
    )
    response["cursor"] = {"next": object_page.next_cursor}
    return response


@router.get("/objects/tree")
def get_storage_object_tree(
    request: Request,
    db: DbSession,
    bucket: str = Query(...),
    prefix: str = Query("", max_length=512),
    _current_user=Depends(data_reader),
):
    if bucket not in settings.minio_bucket_names:
        raise AppHTTPException(422, "INVALID_BUCKET", "Bucket is not configured.")
    normalized_prefix = prefix.strip().lstrip("/")
    if normalized_prefix and not normalized_prefix.endswith("/"):
        normalized_prefix += "/"
    db.rollback()
    storage = storage_service.get_storage_backend()
    cursor: str | None = None
    items = []
    try:
        while len(items) < 5000:
            page = storage.list_objects(
                bucket,
                prefix=normalized_prefix,
                cursor=cursor,
                page_size=200,
            )
            items.extend(page.items)
            cursor = page.next_cursor
            if not cursor:
                break
    except StorageError as exc:
        raise AppHTTPException(
            503,
            "STORAGE_LIST_FAILED",
            "Storage structure could not be listed.",
        ) from exc

    folders: dict[str, dict[str, str]] = {}
    direct_objects = []
    for item in items:
        remainder = item.storage_key[len(normalized_prefix) :]
        if "/" in remainder:
            folder_name = remainder.split("/", 1)[0]
            folder_prefix = f"{normalized_prefix}{folder_name}/"
            folders[folder_prefix] = {"name": folder_name, "prefix": folder_prefix}
        elif remainder:
            direct_objects.append(item)

    registered = registered_files_by_storage_key(
        db,
        bucket=bucket,
        keys=[item.storage_key for item in direct_objects],
    )
    return ok(
        {
            "bucket": bucket,
            "prefix": normalized_prefix,
            "folders": [folders[key] for key in sorted(folders)],
            "objects": [
                storage_object_data(item, registered)
                for item in sorted(direct_objects, key=lambda row: row.storage_key)
            ],
            "truncated": cursor is not None,
        },
        request.state.request_id,
    )


@router.get("/transfers")
def list_transfers(
    request: Request,
    db: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    direction: str | None = Query(default=None),
    status: str | None = Query(default=None),
    operation: str | None = Query(default=None),
    file_id: int | None = Query(default=None),
    _current_user=Depends(data_reader),
):
    rows, total = query_transfers(
        db,
        page=page,
        page_size=page_size,
        direction=direction,
        status=status,
        operation=operation,
        file_id=file_id,
    )
    return page_response(
        [transfer_data(row) for row in rows],
        page,
        page_size,
        total,
        request.state.request_id,
    )


@router.get("/transfers/{transfer_uid}")
def get_transfer(
    transfer_uid: str,
    request: Request,
    db: DbSession,
    _current_user=Depends(data_reader),
):
    row = db.scalar(select(FileTransfer).where(FileTransfer.transfer_uid == transfer_uid))
    if row is None:
        raise not_found("FileTransfer")
    return ok(transfer_data(row), request.state.request_id)
