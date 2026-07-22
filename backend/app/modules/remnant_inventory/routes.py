from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, save_upload_file
from app.modules.identity.interface import CurrentUser, User
from app.modules.operations.audit.interface import write_audit_log
from app.modules.remnant_inventory.access import can_manage_materials, can_use_remnants
from app.modules.remnant_inventory.execution import (
    dispatch_import_execution,
    prepare_import_execution,
)
from app.modules.remnant_inventory.imports import (
    _require_item_access,
    bulk_apply_thickness,
    cancel_import_batch,
    confirm_import_items,
    register_import_batch,
    retry_import_item,
    update_import_item,
)
from app.modules.remnant_inventory.inventory import (
    archive_remnant,
    build_original_download,
    mark_remnant_used,
    preview_file_id,
    release_remnant,
    reserve_remnant,
    search_remnants,
    update_remnant,
)
from app.modules.remnant_inventory.materials import (
    create_material,
    list_materials,
    replace_aliases,
    update_material,
)
from app.modules.remnant_inventory.models import (
    Remnant,
    RemnantImportBatch,
    RemnantImportItem,
    RemnantMaterial,
    RemnantMaterialAlias,
    RemnantPart,
)
from app.modules.remnant_inventory.schemas import (
    BulkThicknessUpdate,
    ImportConfirmRequest,
    ImportItemUpdate,
    MaterialAliasReplace,
    MaterialCreate,
    MaterialRead,
    MaterialUpdate,
    RemnantReserveRequest,
    RemnantUpdate,
)
from app.platform.config.settings import settings
from app.platform.database.session import get_db
from app.platform.http.envelopes import ok, page
from app.platform.http.exceptions import AppHTTPException


def ensure_remnant_inventory_enabled() -> None:
    if not settings.remnant_inventory_enabled:
        raise AppHTTPException(
            404,
            "REMNANT_INVENTORY_DISABLED",
            "Remnant inventory is not enabled.",
        )


def _require_user(actor: User) -> None:
    if not can_use_remnants(actor):
        raise AppHTTPException(403, "REMNANT_FORBIDDEN", "Remnant inventory access denied.")


def _require_admin(actor: User) -> None:
    if not can_manage_materials(actor):
        raise AppHTTPException(403, "REMNANT_ADMIN_REQUIRED", "Administrator access required.")


def _material_data(db: Session, row: RemnantMaterial) -> dict:
    data = MaterialRead.model_validate(row).model_dump(mode="json")
    data["aliases"] = list(
        db.scalars(
            select(RemnantMaterialAlias.alias)
            .where(RemnantMaterialAlias.material_id == row.id)
            .order_by(RemnantMaterialAlias.id)
        ).all()
    )
    return data


def _item_data(db: Session, item: RemnantImportItem) -> dict:
    source = db.get(StoredFile, item.source_file_id)
    return {
        "id": item.id,
        "batch_id": item.batch_id,
        "source_file_id": item.source_file_id,
        "dxf_file_id": item.dxf_file_id,
        "original_name": source.original_name if source else None,
        "source_ext": item.source_ext,
        "attempt": item.attempt,
        "status": item.status,
        "material_candidates": item.material_candidates_json or [],
        "project_candidates": item.project_candidates_json or [],
        "part_candidates": item.part_candidates_json or [],
        "warnings": item.warnings_json or [],
        "thickness_mm": str(item.corrected_thickness_mm) if item.corrected_thickness_mm else None,
        "material_id": item.corrected_material_id,
        "project_no": item.corrected_project_no,
        "parts": item.corrected_parts_json or [],
        "error_code": item.error_code,
        "error_message": item.error_message,
    }


def _batch_data(db: Session, batch: RemnantImportBatch, *, include_items: bool = True) -> dict:
    data = {
        "id": batch.id,
        "created_by": batch.created_by,
        "status": batch.status,
        "total_count": batch.total_count,
        "converting_count": batch.converting_count,
        "parsing_count": batch.parsing_count,
        "pending_count": batch.pending_count,
        "confirmed_count": batch.confirmed_count,
        "failed_count": batch.failed_count,
        "cancelled_count": batch.cancelled_count,
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
    }
    if include_items:
        items = db.scalars(
            select(RemnantImportItem)
            .where(RemnantImportItem.batch_id == batch.id)
            .order_by(RemnantImportItem.id)
        ).all()
        data["items"] = [_item_data(db, item) for item in items]
    return data


def _remnant_data(db: Session, row: Remnant) -> dict:
    source = db.get(StoredFile, row.source_file_id)
    material = db.get(RemnantMaterial, row.material_id)
    reserver = db.get(User, row.reserved_by) if row.reserved_by else None
    parts = list(
        db.scalars(
            select(RemnantPart.part_no)
            .where(RemnantPart.remnant_id == row.id)
            .order_by(RemnantPart.id)
        ).all()
    )
    return {
        "id": row.id,
        "source_file_id": row.source_file_id,
        "dxf_file_id": row.dxf_file_id,
        "source_name": source.original_name if source else None,
        "source_ext": source.file_ext if source else None,
        "thickness_mm": str(row.thickness_mm),
        "material_id": row.material_id,
        "material_code": material.code if material else None,
        "project_no": row.project_no,
        "parts": parts,
        "status": row.status,
        "imported_by": row.imported_by,
        "reserved_by": row.reserved_by,
        "reserved_by_name": reserver.real_name if reserver else None,
        "reserved_at": row.reserved_at,
        "used_by": row.used_by,
        "used_at": row.used_at,
        "version": row.version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


_enabled = [Depends(ensure_remnant_inventory_enabled)]
materials_router = APIRouter(dependencies=_enabled)
imports_router = APIRouter(dependencies=_enabled)
import_items_router = APIRouter(dependencies=_enabled)
remnants_router = APIRouter(dependencies=_enabled)


@materials_router.get("")
def get_materials(
    request: Request,
    current_user: CurrentUser,
    enabled_only: bool = True,
    db: Session = Depends(get_db),
):
    _require_user(current_user)
    return ok(
        [_material_data(db, row) for row in list_materials(db, enabled_only=enabled_only)],
        request.state.request_id,
    )


@materials_router.post("", status_code=status.HTTP_201_CREATED)
def post_material(
    payload: MaterialCreate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    row = create_material(
        db, code=payload.code, family_code=payload.family_code, actor_id=current_user.id
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.material.create",
        resource_type="remnant_material",
        resource_id=row.id,
        after_json={"code": row.code, "family_code": row.family_code},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ok(_material_data(db, row), request.state.request_id)


@materials_router.patch("/{material_id}")
def patch_material(
    material_id: int,
    payload: MaterialUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    row = update_material(
        db,
        material_id,
        family_code=payload.family_code,
        enabled=payload.enabled,
        actor_id=current_user.id,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.material.update",
        resource_type="remnant_material",
        resource_id=row.id,
        after_json={"family_code": row.family_code, "enabled": row.enabled},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ok(_material_data(db, row), request.state.request_id)


@materials_router.put("/{material_id}/aliases")
def put_material_aliases(
    material_id: int,
    payload: MaterialAliasReplace,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    material = db.get(RemnantMaterial, material_id)
    if material is None:
        raise AppHTTPException(404, "REMNANT_MATERIAL_NOT_FOUND", "Material not found.")
    rows = replace_aliases(db, material=material, aliases=payload.aliases, actor_id=current_user.id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.material.aliases",
        resource_type="remnant_material",
        resource_id=material.id,
        after_json={"aliases": [row.alias for row in rows]},
        request=request,
    )
    db.commit()
    return ok(
        [{"id": row.id, "alias": row.alias} for row in rows],
        request.state.request_id,
    )


@imports_router.post("", status_code=status.HTTP_202_ACCEPTED)
async def post_import_batch(
    request: Request,
    current_user: CurrentUser,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    _require_user(current_user)
    if not files or len(files) > settings.remnant_import_max_files:
        raise AppHTTPException(
            422,
            "REMNANT_IMPORT_FILE_COUNT_INVALID",
            "Import file count is invalid.",
            {"max_files": settings.remnant_import_max_files},
        )
    stored_files: list[StoredFile] = []
    try:
        for upload in files:
            stored_files.append(
                await save_upload_file(
                    db,
                    upload,
                    uploaded_by=current_user.id,
                    batch_name=f"remnant-{request.state.request_id}",
                    request_id=request.state.request_id,
                    transfer_operation="remnant_import",
                )
            )
        batch = register_import_batch(db, actor_id=current_user.id, source_files=stored_files)
        dispatch = prepare_import_execution(db, batch.id, actor_id=current_user.id)
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="remnants.import",
            resource_type="remnant_import_batch",
            resource_id=batch.id,
            after_json={"file_count": len(files)},
            request=request,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    dispatch_import_execution(dispatch)
    db.refresh(batch)
    return ok(_batch_data(db, batch), request.state.request_id)


def _batch_with_access(db: Session, batch_id: int, actor: User) -> RemnantImportBatch:
    batch = db.get(RemnantImportBatch, batch_id)
    if batch is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "Import batch not found.")
    item = db.scalar(
        select(RemnantImportItem).where(RemnantImportItem.batch_id == batch.id).limit(1)
    )
    if item is not None:
        _require_item_access(db, item, actor)
    return batch


@imports_router.get("/{batch_id}")
def get_import_batch(
    batch_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    return ok(
        _batch_data(db, _batch_with_access(db, batch_id, current_user)),
        request.state.request_id,
    )


@imports_router.post("/{batch_id}/bulk-thickness")
def post_bulk_thickness(
    batch_id: int,
    payload: BulkThicknessUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    changed = bulk_apply_thickness(
        db,
        batch_id,
        item_ids=payload.item_ids,
        thickness_mm=payload.thickness_mm,
        actor=current_user,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.bulk_thickness",
        resource_type="remnant_import_batch",
        resource_id=batch_id,
        after_json={"item_ids": changed, "thickness_mm": str(payload.thickness_mm)},
        request=request,
    )
    db.commit()
    return ok({"updated_item_ids": changed}, request.state.request_id)


@imports_router.post("/{batch_id}/cancel")
def post_cancel_batch(
    batch_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    cancelled = cancel_import_batch(
        db,
        batch_id,
        actor=current_user,
        request_id=request.state.request_id,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.cancel",
        resource_type="remnant_import_batch",
        resource_id=batch_id,
        after_json={"item_ids": cancelled},
        request=request,
    )
    db.commit()
    return ok({"cancelled_item_ids": cancelled}, request.state.request_id)


@import_items_router.post("/bulk-confirm")
def post_bulk_confirm(
    payload: ImportConfirmRequest,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    result = confirm_import_items(db, payload.item_ids, actor=current_user)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.confirm",
        resource_type="remnant_import_item",
        resource_id=payload.item_ids[0] if payload.item_ids else 0,
        after_json=result.model_dump(mode="json"),
        request=request,
    )
    db.commit()
    return ok(result.model_dump(mode="json"), request.state.request_id)


@import_items_router.patch("/{item_id}")
def patch_import_item(
    item_id: int,
    payload: ImportItemUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    row = update_import_item(
        db,
        item_id,
        actor=current_user,
        thickness_mm=payload.thickness_mm,
        material_id=payload.material_id,
        project_no=payload.project_no,
        parts=payload.parts,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.correct",
        resource_type="remnant_import_item",
        resource_id=row.id,
        after_json={
            "material_id": row.corrected_material_id,
            "project_no": row.corrected_project_no,
            "parts": row.corrected_parts_json,
            "thickness_mm": str(row.corrected_thickness_mm),
        },
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ok(_item_data(db, row), request.state.request_id)


@import_items_router.post("/{item_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def post_retry_item(
    item_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    dispatch = retry_import_item(db, item_id, actor=current_user)
    item = db.get(RemnantImportItem, item_id)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.retry",
        resource_type="remnant_import_item",
        resource_id=item_id,
        after_json={"attempt": item.attempt},
        request=request,
    )
    db.commit()
    dispatch_import_execution(dispatch)
    return ok(
        {"item_id": item_id, "attempt": item.attempt}, request.state.request_id
    )


def _search_response(
    request: Request,
    db: Session,
    *,
    material_id: int,
    thickness_mm: Decimal,
    include_family: bool,
    statuses: list[str] | None,
    page_no: int,
    page_size: int,
):
    result = search_remnants(
        db,
        material_id=material_id,
        thickness_mm=thickness_mm,
        include_family=include_family,
        statuses=statuses,
        page=page_no,
        page_size=page_size,
    )
    return page(
        [_remnant_data(db, row) for row in result.items],
        result.page,
        result.page_size,
        result.total,
        request.state.request_id,
    )


@remnants_router.get("")
@remnants_router.get("/search")
def get_remnants(
    request: Request,
    current_user: CurrentUser,
    material_id: int = Query(..., ge=1),
    thickness_mm: Decimal = Query(..., gt=0),
    include_family: bool = False,
    statuses: list[str] | None = Query(default=None),
    page_no: int = Query(default=1, alias="page", ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _require_user(current_user)
    return _search_response(
        request,
        db,
        material_id=material_id,
        thickness_mm=thickness_mm,
        include_family=include_family,
        statuses=statuses,
        page_no=page_no,
        page_size=page_size,
    )


@remnants_router.get("/{remnant_id}")
def get_remnant(
    remnant_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    _require_user(current_user)
    row = db.get(Remnant, remnant_id)
    if row is None:
        raise AppHTTPException(404, "REMNANT_NOT_FOUND", "Remnant not found.")
    return ok(_remnant_data(db, row), request.state.request_id)


@remnants_router.patch("/{remnant_id}")
def patch_remnant(
    remnant_id: int,
    payload: RemnantUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    row = update_remnant(
        db,
        remnant_id,
        actor=current_user,
        thickness_mm=payload.thickness_mm,
        material_id=payload.material_id,
        project_no=payload.project_no,
        parts=payload.parts,
    )
    db.commit()
    return ok(_remnant_data(db, row), request.state.request_id)


@remnants_router.get("/{remnant_id}/preview")
def get_remnant_preview(
    remnant_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    file_id = preview_file_id(db, remnant_id, actor=current_user)
    return ok(
        {"file_id": file_id, "preview_url": f"/api/v1/files/{file_id}/dxf-preview"},
        request.state.request_id,
    )


@remnants_router.get("/{remnant_id}/original-download")
def get_original_download(
    remnant_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    return ok(
        build_original_download(db, remnant_id, actor=current_user).__dict__,
        request.state.request_id,
    )


@remnants_router.post("/{remnant_id}/reserve")
def post_reserve(
    remnant_id: int,
    payload: RemnantReserveRequest,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    row = reserve_remnant(db, remnant_id, actor=current_user, expected_version=payload.version)
    db.commit()
    return ok(_remnant_data(db, row), request.state.request_id)


@remnants_router.post("/{remnant_id}/release")
def post_release(
    remnant_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    row = release_remnant(db, remnant_id, actor=current_user)
    db.commit()
    return ok(_remnant_data(db, row), request.state.request_id)


@remnants_router.post("/{remnant_id}/mark-used")
def post_mark_used(
    remnant_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    row = mark_remnant_used(db, remnant_id, actor=current_user)
    db.commit()
    return ok(_remnant_data(db, row), request.state.request_id)


@remnants_router.post("/{remnant_id}/archive")
def post_archive(
    remnant_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    row = archive_remnant(db, remnant_id, actor=current_user)
    db.commit()
    return ok(_remnant_data(db, row), request.state.request_id)
