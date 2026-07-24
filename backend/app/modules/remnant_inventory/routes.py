from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
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
from app.modules.remnant_inventory.export import (
    EXCEL_CONTENT_TYPE,
    CleanupFileResponse,
    build_remnant_export,
)
from app.modules.remnant_inventory.imports import (
    _require_item_access,
    bulk_apply_optional_metadata,
    bulk_apply_project,
    bulk_apply_thickness,
    cancel_import_batch,
    cancel_import_item,
    confirm_import_items,
    register_import_batch,
    retry_import_item,
    update_import_item,
)
from app.modules.remnant_inventory.inventory import (
    archive_remnant,
    build_original_download,
    bulk_archive_remnants,
    delete_archived_remnant,
    list_all_remnants,
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
    resolve_or_create_material,
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
    BulkOptionalMetadataUpdate,
    BulkProjectUpdate,
    BulkThicknessUpdate,
    ImportConfirmRequest,
    ImportItemUpdate,
    ImportMaterialResolveCreate,
    MaterialAliasReplace,
    MaterialCreate,
    MaterialRead,
    MaterialResolveCreate,
    MaterialStatusUpdate,
    MaterialUpdate,
    RemnantBulkArchiveFailure,
    RemnantBulkArchiveRequest,
    RemnantBulkArchiveResult,
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
            "余料库功能尚未启用。",
        )


def _require_user(actor: User) -> None:
    if not can_use_remnants(actor):
        raise AppHTTPException(403, "REMNANT_FORBIDDEN", "当前账号无权使用余料库。")


def _require_admin(actor: User) -> None:
    if not can_manage_materials(actor):
        raise AppHTTPException(403, "REMNANT_ADMIN_REQUIRED", "该操作需要管理员权限。")


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
        "source_relative_path": item.source_relative_path,
        "attempt": item.attempt,
        "status": item.status,
        "material_candidates": item.material_candidates_json or [],
        "project_candidates": item.project_candidates_json or [],
        "part_candidates": item.part_candidates_json or [],
        "warnings": item.warnings_json or [],
        "standard_parse": item.standard_parse_json,
        "thickness_mm": str(item.corrected_thickness_mm) if item.corrected_thickness_mm else None,
        "material_id": item.corrected_material_id,
        "project_no": item.corrected_project_no,
        "project_no_secondary": item.corrected_project_no_secondary,
        "storage_location": item.corrected_storage_location,
        "remark_1": item.corrected_remark_1,
        "remark_2": item.corrected_remark_2,
        "parts": item.corrected_parts_json or [],
        "error_code": item.error_code,
        "error_message": item.error_message,
    }


def _batch_data(db: Session, batch: RemnantImportBatch, *, include_items: bool = True) -> dict:
    data = {
        "id": batch.id,
        "created_by": batch.created_by,
        "import_mode": batch.import_mode,
        "default_project_no": batch.default_project_no,
        "source_folder_name": batch.source_folder_name,
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
        "project_no_secondary": row.project_no_secondary,
        "storage_location": row.storage_location,
        "remark_1": row.remark_1,
        "remark_2": row.remark_2,
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


@materials_router.post("/resolve-or-create")
def post_resolve_or_create_material(
    payload: MaterialResolveCreate,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    row, created = resolve_or_create_material(
        db, code=payload.code, actor_id=current_user.id
    )
    if created:
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="remnants.material.create",
            resource_type="remnant_material",
            resource_id=row.id,
            after_json={"code": row.code, "family_code": row.family_code},
            request=request,
        )
        response.status_code = status.HTTP_201_CREATED
    db.commit()
    db.refresh(row)
    return ok(
        {"material": _material_data(db, row), "created": created},
        request.state.request_id,
    )


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


@materials_router.patch("/{material_id}/status")
def patch_material_status(
    material_id: int,
    payload: MaterialStatusUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    _require_user(current_user)
    if payload.enabled is None:
        raise AppHTTPException(
            422,
            "REMNANT_MATERIAL_STATUS_REQUIRED",
            "请提供材质启停状态。",
        )
    if type(payload.enabled) is not bool:
        raise AppHTTPException(
            422,
            "REMNANT_MATERIAL_STATUS_INVALID",
            "材质启停状态必须为布尔值。",
        )
    row = db.get(RemnantMaterial, material_id)
    if row is None:
        raise AppHTTPException(404, "REMNANT_MATERIAL_NOT_FOUND", "材质不存在或已被删除。")
    before = row.enabled
    row = update_material(
        db,
        material_id,
        enabled=payload.enabled,
        actor_id=current_user.id,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.material.status",
        resource_type="remnant_material",
        resource_id=row.id,
        before_json={"enabled": before},
        after_json={"enabled": row.enabled},
        request=request,
    )
    db.commit()
    db.refresh(row)
    return ok(
        {
            "material": _material_data(db, row),
            "message": "材质已启用。" if row.enabled else "材质已停用。",
        },
        request.state.request_id,
    )


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
        raise AppHTTPException(404, "REMNANT_MATERIAL_NOT_FOUND", "材质不存在或已被删除。")
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
            "导入图纸数量不正确。",
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


@imports_router.post("/auto", status_code=status.HTTP_202_ACCEPTED)
async def post_auto_import_batch(
    request: Request,
    current_user: CurrentUser,
    files: list[UploadFile] | None = File(None),
    relative_paths: list[str] | None = Form(None),
    project_no: str | None = Form(None),
    folder_name: str | None = Form(None),
    db: Session = Depends(get_db),
):
    _require_user(current_user)
    if not files:
        raise AppHTTPException(
            422,
            "REMNANT_IMPORT_EMPTY",
            "请至少选择一张图纸。",
        )
    if len(files) > settings.remnant_import_max_files:
        raise AppHTTPException(
            422,
            "REMNANT_IMPORT_FILE_COUNT_INVALID",
            "导入图纸数量不正确。",
            {"max_files": settings.remnant_import_max_files},
        )
    if relative_paths is None:
        raise AppHTTPException(
            422,
            "REMNANT_SOURCE_PATH_REQUIRED",
            "请提供与图纸一一对应的相对路径。",
        )
    if len(relative_paths) != len(files):
        raise AppHTTPException(
            422,
            "REMNANT_SOURCE_PATH_COUNT_MISMATCH",
            "图纸文件与相对路径数量不一致。",
        )
    if project_no is None or not project_no.strip():
        raise AppHTTPException(422, "REMNANT_PROJECT_REQUIRED", "请填写项目编号。")
    stored_files: list[StoredFile] = []
    try:
        for upload in files:
            stored_files.append(
                await save_upload_file(
                    db,
                    upload,
                    uploaded_by=current_user.id,
                    batch_name=f"remnant-auto-{request.state.request_id}",
                    request_id=request.state.request_id,
                    transfer_operation="remnant_auto_import",
                )
            )
        batch = register_import_batch(
            db,
            actor_id=current_user.id,
            source_files=stored_files,
            import_mode="auto",
            default_project_no=project_no,
            source_folder_name=folder_name,
            source_relative_paths=relative_paths,
        )
        dispatch = prepare_import_execution(db, batch.id, actor_id=current_user.id)
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="remnants.import.auto",
            resource_type="remnant_import_batch",
            resource_id=batch.id,
            after_json={
                "file_count": len(files),
                "default_project_no": batch.default_project_no,
                "source_folder_name": batch.source_folder_name,
            },
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
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "导入批次不存在或已被删除。")
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


@imports_router.post("/{batch_id}/bulk-project")
def post_bulk_project(
    batch_id: int,
    payload: BulkProjectUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if payload.item_ids is None or payload.item_ids == []:
        raise AppHTTPException(
            422,
            "REMNANT_IMPORT_ITEMS_REQUIRED",
            "请选择需要设置项目的图纸。",
        )
    if (
        not isinstance(payload.item_ids, list)
        or len(payload.item_ids) > 1000
        or any(type(item_id) is not int or item_id < 1 for item_id in payload.item_ids)
    ):
        raise AppHTTPException(
            422,
            "REMNANT_IMPORT_ITEM_IDS_INVALID",
            "图纸编号列表格式不正确。",
        )
    if payload.project_no is None:
        raise AppHTTPException(
            422,
            "REMNANT_PROJECT_REQUIRED",
            "请填写项目编号。",
        )
    if not isinstance(payload.project_no, str):
        raise AppHTTPException(
            422,
            "REMNANT_PROJECT_INVALID",
            "项目编号格式不正确。",
        )
    changed = bulk_apply_project(
        db,
        batch_id,
        item_ids=payload.item_ids,
        project_no=payload.project_no,
        actor=current_user,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.bulk_project",
        resource_type="remnant_import_batch",
        resource_id=batch_id,
        after_json={"item_ids": changed, "project_no": payload.project_no.strip()},
        request=request,
    )
    db.commit()
    return ok({"updated_item_ids": changed}, request.state.request_id)


@imports_router.post("/{batch_id}/bulk-optional-metadata")
def post_bulk_optional_metadata(
    batch_id: int,
    payload: BulkOptionalMetadataUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    optional_fields = {
        "project_no_secondary",
        "storage_location",
        "remark_1",
        "remark_2",
    }
    updates = {
        field: getattr(payload, field)
        for field in optional_fields
        if field in payload.model_fields_set
    }
    changed = bulk_apply_optional_metadata(
        db,
        batch_id,
        item_ids=payload.item_ids,
        actor=current_user,
        **updates,
    )
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.bulk_optional_metadata",
        resource_type="remnant_import_batch",
        resource_id=batch_id,
        after_json={
            "item_ids": changed,
            **updates,
        },
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


@import_items_router.post("/{item_id}/resolve-material")
def post_resolve_import_material(
    item_id: int,
    payload: ImportMaterialResolveCreate,
    request: Request,
    response: Response,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    if not isinstance(payload.code, str) or not payload.code.strip():
        raise AppHTTPException(
            422,
            "REMNANT_MATERIAL_INVALID",
            "请填写完整的材质牌号。",
        )
    if len(payload.code.strip()) > 64:
        raise AppHTTPException(
            422,
            "REMNANT_MATERIAL_INVALID",
            "材质牌号不能超过 64 个字符。",
        )
    item = db.get(RemnantImportItem, item_id)
    if item is None:
        raise AppHTTPException(
            404,
            "REMNANT_IMPORT_ITEM_NOT_FOUND",
            "导入图纸不存在或已被删除。",
        )
    _require_item_access(db, item, current_user)
    if item.status != "pending_confirmation":
        raise AppHTTPException(
            409,
            "REMNANT_IMPORT_ITEM_LOCKED",
            "该图纸当前不可补录材质。",
        )
    row, created = resolve_or_create_material(
        db,
        code=payload.code,
        actor_id=current_user.id,
    )
    action = "remnants.import.material.create" if created else "remnants.import.material.resolve"
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action=action,
        resource_type="remnant_import_item",
        resource_id=item.id,
        after_json={"material_id": row.id, "code": row.code},
        request=request,
    )
    if created:
        response.status_code = status.HTTP_201_CREATED
    db.commit()
    db.refresh(row)
    return ok(
        {"material": _material_data(db, row), "created": created},
        request.state.request_id,
    )


@import_items_router.patch("/{item_id}")
def patch_import_item(
    item_id: int,
    payload: ImportItemUpdate,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    values = payload.model_dump(exclude_unset=True)
    row = update_import_item(db, item_id, actor=current_user, **values)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.correct",
        resource_type="remnant_import_item",
        resource_id=row.id,
        after_json={
            "material_id": row.corrected_material_id,
            "project_no": row.corrected_project_no,
            "project_no_secondary": row.corrected_project_no_secondary,
            "storage_location": row.corrected_storage_location,
            "remark_1": row.corrected_remark_1,
            "remark_2": row.corrected_remark_2,
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


@import_items_router.post("/{item_id}/cancel")
def post_cancel_item(
    item_id: int,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    item = cancel_import_item(db, item_id, actor=current_user)
    write_audit_log(
        db,
        actor_user_id=current_user.id,
        action="remnants.import.cancel_item",
        resource_type="remnant_import_item",
        resource_id=item.id,
        after_json={"status": item.status},
        request=request,
    )
    db.commit()
    db.refresh(item)
    return ok(_item_data(db, item), request.state.request_id)


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


@remnants_router.get("/all")
def get_all_remnants(
    request: Request,
    current_user: CurrentUser,
    material_id: int | None = Query(default=None, ge=1),
    thickness_mm: Decimal | None = Query(default=None, gt=0),
    statuses: list[str] | None = Query(default=None),
    project: str | None = Query(default=None, max_length=128),
    project_secondary: str | None = Query(default=None, max_length=128),
    storage_location: str | None = Query(default=None, max_length=128),
    remark_1: str | None = Query(default=None, max_length=500),
    remark_2: str | None = Query(default=None, max_length=500),
    part: str | None = Query(default=None, max_length=128),
    sort: str = Query(default="created_desc"),
    page_no: int = Query(default=1, alias="page", ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    _require_user(current_user)
    result = list_all_remnants(
        db,
        material_id=material_id,
        thickness_mm=thickness_mm,
        statuses=statuses,
        project=project,
        project_secondary=project_secondary,
        storage_location=storage_location,
        remark_1=remark_1,
        remark_2=remark_2,
        part=part,
        sort=sort,
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


@remnants_router.get("/export.xlsx")
def get_remnants_export(
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    _require_user(current_user)
    prepared = build_remnant_export(db)
    try:
        write_audit_log(
            db,
            actor_user_id=current_user.id,
            action="remnants.export",
            resource_type="remnant_export",
            resource_id=0,
            after_json={"row_count": prepared.row_count, "file_name": prepared.filename},
            request=request,
        )
        db.commit()
    except BaseException:
        prepared.path.unlink(missing_ok=True)
        raise
    return CleanupFileResponse(
        prepared.path,
        media_type=EXCEL_CONTENT_TYPE,
        filename=prepared.filename,
    )


@remnants_router.post("/bulk-archive")
def post_bulk_archive(
    payload: RemnantBulkArchiveRequest,
    request: Request,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    result = bulk_archive_remnants(db, payload.remnant_ids, actor=current_user)
    db.commit()
    response = RemnantBulkArchiveResult(
        archived=result.archived,
        failed=[
            RemnantBulkArchiveFailure(
                remnant_id=item.remnant_id,
                code=item.code,
                message=item.message,
            )
            for item in result.failed
        ],
    )
    return ok(response.model_dump(), request.state.request_id)


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
        raise AppHTTPException(404, "REMNANT_NOT_FOUND", "余料不存在或已被删除。")
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
        **payload.model_dump(exclude_unset=True),
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


@remnants_router.delete("/{remnant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_remnant(
    remnant_id: int,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
):
    delete_archived_remnant(db, remnant_id, actor=current_user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
