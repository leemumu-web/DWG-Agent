from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, soft_delete_file_in_transaction
from app.modules.identity.interface import User, user_role_codes
from app.modules.jobs.interface import Job, cancel_job
from app.modules.remnant_inventory.execution import (
    ExecutionDispatch,
    prepare_import_execution,
    recalculate_batch_counters,
)
from app.modules.remnant_inventory.models import (
    Remnant,
    RemnantImportBatch,
    RemnantImportItem,
    RemnantMaterial,
    RemnantPart,
)
from app.modules.remnant_inventory.schemas import (
    ImportConfirmationEntry,
    ImportConfirmationResult,
)
from app.platform.config.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.platform.config.settings import settings
from app.platform.http.exceptions import AppHTTPException

REMNANT_SOURCE_EXTENSIONS = {".dwg", ".dxf"}
_ADMIN_ROLES = {ROLE_ADMIN, ROLE_SUPER_ADMIN}
_PERMANENT_IMPORT_ERRORS = {
    "REMNANT_SOURCE_DUPLICATE",
    "REMNANT_SOURCE_DUPLICATE_IN_BATCH",
}
_UNSET = object()


def _require_item_access(db: Session, item: RemnantImportItem, actor: User) -> RemnantImportBatch:
    batch = db.get(RemnantImportBatch, item.batch_id)
    if batch is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "导入批次不存在或已被删除。")
    if batch.created_by != actor.id and not (_ADMIN_ROLES & user_role_codes(actor)):
        raise AppHTTPException(403, "REMNANT_IMPORT_FORBIDDEN", "无权访问该导入批次。")
    return batch


def _thickness(value: Decimal | str | int | float) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AppHTTPException(422, "REMNANT_THICKNESS_INVALID", "余料厚度格式不正确。") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed.as_tuple().exponent < -3:
        raise AppHTTPException(
            422,
            "REMNANT_THICKNESS_INVALID",
            "余料厚度必须大于 0，且最多保留三位小数。",
        )
    return parsed.quantize(Decimal("0.001"))


def _parts(values: Sequence[str]) -> list[str]:
    normalized = [
        unicodedata.normalize("NFKC", value).strip()
        for value in values
        if unicodedata.normalize("NFKC", value).strip()
    ]
    return list(dict.fromkeys(normalized))


def _optional_text(value: str | None) -> str | None:
    return unicodedata.normalize("NFKC", value or "").strip() or None


def normalize_source_relative_path(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or (len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":")
    ):
        raise AppHTTPException(
            422,
            "REMNANT_SOURCE_PATH_INVALID",
            "图纸相对路径格式不正确。",
        )
    if len(normalized) > 1024:
        raise AppHTTPException(
            422,
            "REMNANT_SOURCE_PATH_TOO_LONG",
            "图纸相对路径不能超过 1024 个字符。",
        )
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise AppHTTPException(
            422,
            "REMNANT_SOURCE_PATH_INVALID",
            "图纸相对路径格式不正确。",
        )
    return "/".join(parts)


def _source_folder_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value).strip().replace("\\", "/")
    if not normalized:
        return None
    if normalized.startswith("/") or (
        len(normalized) >= 2 and normalized[0].isalpha() and normalized[1] == ":"
    ):
        raise AppHTTPException(
            422,
            "REMNANT_SOURCE_FOLDER_INVALID",
            "来源文件夹名称格式不正确。",
        )
    top_level = normalized.split("/", 1)[0].strip()
    if top_level in {"", ".", ".."}:
        raise AppHTTPException(
            422,
            "REMNANT_SOURCE_FOLDER_INVALID",
            "来源文件夹名称格式不正确。",
        )
    if len(top_level) > 255:
        raise AppHTTPException(
            422,
            "REMNANT_SOURCE_FOLDER_TOO_LONG",
            "来源文件夹名称不能超过 255 个字符。",
        )
    return top_level


def update_import_item(
    db: Session,
    item_id: int,
    *,
    actor: User,
    thickness_mm: Decimal | str | int | float | None = None,
    material_id: int | None = None,
    project_no: str | None = None,
    project_no_secondary: str | None | object = _UNSET,
    storage_location: str | None | object = _UNSET,
    remark_1: str | None | object = _UNSET,
    remark_2: str | None | object = _UNSET,
    parts: Sequence[str] | None = None,
) -> RemnantImportItem:
    item = db.get(RemnantImportItem, item_id)
    if item is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_ITEM_NOT_FOUND", "导入图纸不存在或已被删除。")
    _require_item_access(db, item, actor)
    if item.status != "pending_confirmation":
        raise AppHTTPException(409, "REMNANT_IMPORT_ITEM_LOCKED", "该图纸当前不可编辑。")
    if thickness_mm is not None:
        item.corrected_thickness_mm = _thickness(thickness_mm)
    if material_id is not None:
        material = db.get(RemnantMaterial, material_id)
        if material is None or not material.enabled:
            raise AppHTTPException(422, "REMNANT_MATERIAL_INVALID", "请选择已启用的材质。")
        item.corrected_material_id = material.id
    if project_no is not None:
        item.corrected_project_no = unicodedata.normalize("NFKC", project_no).strip()
    for value, attribute in (
        (project_no_secondary, "corrected_project_no_secondary"),
        (storage_location, "corrected_storage_location"),
        (remark_1, "corrected_remark_1"),
        (remark_2, "corrected_remark_2"),
    ):
        if value is not _UNSET:
            setattr(item, attribute, _optional_text(value if isinstance(value, str) else None))
    if parts is not None:
        item.corrected_parts_json = _parts(parts)
    db.flush()
    return item


def bulk_apply_thickness(
    db: Session,
    batch_id: int,
    *,
    item_ids: Sequence[int],
    thickness_mm: Decimal | str | int | float,
    actor: User,
) -> list[int]:
    thickness = _thickness(thickness_mm)
    selected = list(
        db.scalars(
            select(RemnantImportItem).where(
                RemnantImportItem.batch_id == batch_id,
                RemnantImportItem.id.in_(set(item_ids)),
            )
        ).all()
    )
    if selected:
        _require_item_access(db, selected[0], actor)
    changed: list[int] = []
    for item in selected:
        if item.status == "pending_confirmation":
            item.corrected_thickness_mm = thickness
            changed.append(item.id)
    db.flush()
    return changed


def bulk_apply_project(
    db: Session,
    batch_id: int,
    *,
    item_ids: Sequence[int],
    project_no: str,
    actor: User,
) -> list[int]:
    normalized = unicodedata.normalize("NFKC", project_no).strip()
    if not normalized:
        raise AppHTTPException(422, "REMNANT_PROJECT_REQUIRED", "请填写项目编号。")
    if len(normalized) > 128:
        raise AppHTTPException(422, "REMNANT_PROJECT_INVALID", "项目编号不能超过 128 个字符。")
    batch = db.get(RemnantImportBatch, batch_id)
    if batch is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "导入批次不存在或已被删除。")
    if batch.created_by != actor.id and not (_ADMIN_ROLES & user_role_codes(actor)):
        raise AppHTTPException(403, "REMNANT_IMPORT_FORBIDDEN", "无权访问该导入批次。")
    selected = list(
        db.scalars(
            select(RemnantImportItem)
            .where(
                RemnantImportItem.batch_id == batch_id,
                RemnantImportItem.id.in_(set(item_ids)),
            )
            .order_by(RemnantImportItem.id)
        ).all()
    )
    changed: list[int] = []
    for item in selected:
        if item.status in {"confirmed", "cancelled"}:
            continue
        item.corrected_project_no = normalized
        changed.append(item.id)
    db.flush()
    return changed


def bulk_apply_optional_metadata(
    db: Session,
    batch_id: int,
    *,
    item_ids: Sequence[int],
    actor: User,
    project_no_secondary: str | None | object = _UNSET,
    storage_location: str | None | object = _UNSET,
    remark_1: str | None | object = _UNSET,
    remark_2: str | None | object = _UNSET,
) -> list[int]:
    supplied_values = {
        attribute: value
        for attribute, value in (
            ("corrected_project_no_secondary", project_no_secondary),
            ("corrected_storage_location", storage_location),
            ("corrected_remark_1", remark_1),
            ("corrected_remark_2", remark_2),
        )
        if value is not _UNSET
    }
    if not supplied_values:
        raise AppHTTPException(
            422,
            "REMNANT_OPTIONAL_METADATA_REQUIRED",
            "请至少选择一项需要批量更新的附加信息。",
        )
    batch = db.get(RemnantImportBatch, batch_id)
    if batch is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "导入批次不存在或已被删除。")
    if batch.created_by != actor.id and not (_ADMIN_ROLES & user_role_codes(actor)):
        raise AppHTTPException(403, "REMNANT_IMPORT_FORBIDDEN", "无权访问该导入批次。")
    selected = list(
        db.scalars(
            select(RemnantImportItem)
            .where(
                RemnantImportItem.batch_id == batch_id,
                RemnantImportItem.id.in_(set(item_ids)),
            )
            .order_by(RemnantImportItem.id)
        ).all()
    )
    changed: list[int] = []
    for item in selected:
        if item.status in {"confirmed", "cancelled"}:
            continue
        for attribute, value in supplied_values.items():
            setattr(item, attribute, _optional_text(value if isinstance(value, str) else None))
        changed.append(item.id)
    db.flush()
    return changed


def retry_import_item(db: Session, item_id: int, *, actor: User) -> ExecutionDispatch:
    item = db.get(RemnantImportItem, item_id)
    if item is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_ITEM_NOT_FOUND", "导入图纸不存在或已被删除。")
    batch = _require_item_access(db, item, actor)
    if item.status != "failed":
        raise AppHTTPException(409, "REMNANT_IMPORT_RETRY_INVALID", "只有处理失败的图纸可以重试。")
    if item.error_code in _PERMANENT_IMPORT_ERRORS:
        raise AppHTTPException(
            409,
            "REMNANT_IMPORT_RETRY_INVALID",
            "重复图纸不能重试，请取消该行。",
        )
    item.attempt += 1
    item.status = "uploaded"
    item.error_code = None
    item.error_message = None
    item.conversion_job_id = None
    item.parse_job_id = None
    if item.source_ext == ".dwg":
        item.dxf_file_id = None
    db.flush()
    return prepare_import_execution(db, batch.id, actor_id=actor.id)


def cancel_import_item(
    db: Session,
    item_id: int,
    *,
    actor: User,
) -> RemnantImportItem:
    item = db.get(RemnantImportItem, item_id)
    if item is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_ITEM_NOT_FOUND", "导入图纸不存在或已被删除。")
    _require_item_access(db, item, actor)
    if item.status in {"confirmed", "cancelled"}:
        raise AppHTTPException(
            409,
            "REMNANT_IMPORT_CANCEL_INVALID",
            "已确认或已取消的图纸不能再次取消。",
        )
    for job_id in (item.conversion_job_id, item.parse_job_id):
        job = db.get(Job, job_id) if job_id is not None else None
        if job is not None and job.status in {
            "pending",
            "queued",
            "running",
            "validating",
            "waiting_cad_worker",
        }:
            cancel_job(db, job)
    item.status = "cancelled"
    db.flush()
    recalculate_batch_counters(db, item.batch_id)
    return item


def cancel_import_batch(
    db: Session,
    batch_id: int,
    *,
    actor: User,
    request_id: str,
) -> list[int]:
    items = list(
        db.scalars(
            select(RemnantImportItem)
            .where(RemnantImportItem.batch_id == batch_id)
            .order_by(RemnantImportItem.id)
        ).all()
    )
    if not items:
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "导入批次不存在或已被删除。")
    _require_item_access(db, items[0], actor)
    cancelled: list[int] = []
    file_ids: set[int] = set()
    for item in items:
        if item.status in {"confirmed", "cancelled"}:
            continue
        for job_id in (item.conversion_job_id, item.parse_job_id):
            job = db.get(Job, job_id) if job_id is not None else None
            if job is not None and job.status in {
                "pending",
                "queued",
                "running",
                "validating",
                "waiting_cad_worker",
            }:
                cancel_job(db, job)
        item.status = "cancelled"
        cancelled.append(item.id)
        file_ids.add(item.source_file_id)
        if item.dxf_file_id is not None:
            file_ids.add(item.dxf_file_id)
    for file_id in file_ids:
        stored = db.get(StoredFile, file_id)
        if stored is not None and stored.status != "deleted":
            soft_delete_file_in_transaction(
                db,
                stored,
                actor_user_id=actor.id,
                request_id=request_id,
                batch_ref=f"remnant-import-{batch_id}",
            )
    db.flush()
    recalculate_batch_counters(db, batch_id)
    return cancelled


def _confirmation_error(db: Session, item: RemnantImportItem) -> str | None:
    if item.status != "pending_confirmation":
        return "REMNANT_IMPORT_ITEM_NOT_READY"
    if item.corrected_thickness_mm is None or item.corrected_thickness_mm <= 0:
        return "REMNANT_THICKNESS_REQUIRED"
    material = db.get(RemnantMaterial, item.corrected_material_id)
    if material is None or not material.enabled:
        return "REMNANT_MATERIAL_REQUIRED"
    if not (item.corrected_project_no or "").strip():
        return "REMNANT_PROJECT_REQUIRED"
    if not _parts(item.corrected_parts_json or []):
        return "REMNANT_PARTS_REQUIRED"
    if item.dxf_file_id is None:
        return "REMNANT_DXF_REQUIRED"
    return None


def confirm_import_items(
    db: Session, item_ids: Sequence[int], *, actor: User
) -> ImportConfirmationResult:
    result = ImportConfirmationResult()
    for item_id in dict.fromkeys(item_ids):
        item = db.get(RemnantImportItem, item_id)
        if item is None:
            result.invalid.append(
                ImportConfirmationEntry(item_id=item_id, code="REMNANT_IMPORT_ITEM_NOT_FOUND")
            )
            continue
        _require_item_access(db, item, actor)
        existing = db.scalar(
            select(Remnant).where(
                or_(
                    Remnant.import_item_id == item.id,
                    Remnant.source_sha256 == item.source_sha256,
                )
            )
        )
        if existing is not None:
            result.already_confirmed.append(
                ImportConfirmationEntry(item_id=item.id, remnant_id=existing.id)
            )
            continue
        error = _confirmation_error(db, item)
        if error:
            result.invalid.append(ImportConfirmationEntry(item_id=item.id, code=error))
            continue
        remnant = Remnant(
            import_item_id=item.id,
            source_file_id=item.source_file_id,
            dxf_file_id=item.dxf_file_id,
            source_sha256=item.source_sha256,
            thickness_mm=item.corrected_thickness_mm,
            material_id=item.corrected_material_id,
            project_no=item.corrected_project_no.strip(),
            project_no_secondary=item.corrected_project_no_secondary,
            storage_location=item.corrected_storage_location,
            remark_1=item.corrected_remark_1,
            remark_2=item.corrected_remark_2,
            imported_by=db.get(RemnantImportBatch, item.batch_id).created_by,
            confirmed_by=actor.id,
            confirmed_at=datetime.now(UTC),
        )
        try:
            with db.begin_nested():
                db.add(remnant)
                db.flush()
        except IntegrityError:
            winner = db.scalar(
                select(Remnant).where(Remnant.source_sha256 == item.source_sha256)
            )
            if winner is None:
                raise
            result.already_confirmed.append(
                ImportConfirmationEntry(item_id=item.id, remnant_id=winner.id)
            )
            continue
        for part_no in _parts(item.corrected_parts_json or []):
            db.add(RemnantPart(remnant_id=remnant.id, part_no=part_no))
        item.status = "confirmed"
        db.flush()
        result.confirmed.append(
            ImportConfirmationEntry(item_id=item.id, remnant_id=remnant.id)
        )
        recalculate_batch_counters(db, item.batch_id)
    return result


def register_import_batch(
    db: Session,
    *,
    actor_id: int,
    source_files: Sequence[StoredFile],
    max_files: int | None = None,
    import_mode: str = "manual",
    default_project_no: str | None = None,
    source_folder_name: str | None = None,
    source_relative_paths: Sequence[str] | None = None,
) -> RemnantImportBatch:
    limit = max_files if max_files is not None else settings.remnant_import_max_files
    if not source_files:
        raise AppHTTPException(422, "REMNANT_IMPORT_EMPTY", "请至少选择一张图纸。")
    if len(source_files) > limit:
        raise AppHTTPException(
            422,
            "REMNANT_IMPORT_TOO_MANY_FILES",
            "导入图纸数量超过单次上限。",
            {"max_files": limit},
        )
    if import_mode not in {"manual", "auto"}:
        raise AppHTTPException(422, "REMNANT_IMPORT_MODE_INVALID", "导入模式不正确。")

    normalized_project = (
        unicodedata.normalize("NFKC", default_project_no).strip()
        if default_project_no is not None
        else None
    )
    normalized_paths: list[str | None]
    if import_mode == "auto":
        if not normalized_project:
            raise AppHTTPException(422, "REMNANT_PROJECT_REQUIRED", "请填写项目编号。")
        if len(normalized_project) > 128:
            raise AppHTTPException(422, "REMNANT_PROJECT_INVALID", "项目编号不能超过 128 个字符。")
        if source_relative_paths is None or len(source_relative_paths) != len(source_files):
            raise AppHTTPException(
                422,
                "REMNANT_SOURCE_PATH_COUNT_MISMATCH",
                "图纸文件与相对路径数量不一致。",
            )
        normalized_paths = [
            normalize_source_relative_path(path) for path in source_relative_paths
        ]
    else:
        normalized_paths = [None] * len(source_files)

    seen: dict[str, StoredFile] = {}
    for source in source_files:
        extension = source.file_ext.lower()
        if extension not in REMNANT_SOURCE_EXTENSIONS:
            raise AppHTTPException(
                415,
                "REMNANT_FILE_TYPE_NOT_ALLOWED",
                "仅支持导入 DWG 和 DXF 图纸。",
            )
        if source.sha256 in seen and import_mode == "manual":
            raise AppHTTPException(
                409,
                "REMNANT_SOURCE_DUPLICATE_IN_BATCH",
                "同一张源图纸在本批次中重复出现。",
                {"first_file_id": seen[source.sha256].id, "duplicate_file_id": source.id},
            )
        seen[source.sha256] = source

    existing_rows = {
        row.source_sha256: row.id
        for row in db.scalars(
            select(Remnant).where(Remnant.source_sha256.in_(list(seen)))
        ).all()
    }
    if existing_rows and import_mode == "manual":
        existing_sha, existing_id = next(iter(existing_rows.items()))
        raise AppHTTPException(
            409,
            "REMNANT_SOURCE_DUPLICATE",
            "该源图纸已存在于余料库中。",
            {"remnant_id": existing_id, "source_sha256": existing_sha},
        )

    batch = RemnantImportBatch(
        created_by=actor_id,
        import_mode=import_mode,
        default_project_no=normalized_project if import_mode == "auto" else None,
        source_folder_name=_source_folder_name(source_folder_name)
        if import_mode == "auto"
        else None,
        status="uploaded",
        total_count=len(source_files),
    )
    db.add(batch)
    db.flush()
    accepted_shas: set[str] = set()
    failed_count = 0
    for source, relative_path in zip(source_files, normalized_paths, strict=True):
        status = "uploaded"
        error_code = None
        error_message = None
        if import_mode == "auto":
            if source.sha256 in accepted_shas:
                status = "failed"
                error_code = "REMNANT_SOURCE_DUPLICATE_IN_BATCH"
                error_message = "同一张源图纸在本批次中重复，已跳过该文件。"
            elif source.sha256 in existing_rows:
                status = "failed"
                error_code = "REMNANT_SOURCE_DUPLICATE"
                error_message = "该源图纸已存在于余料库，已跳过该文件。"
            accepted_shas.add(source.sha256)
        if status == "failed":
            failed_count += 1
        db.add(
            RemnantImportItem(
                batch_id=batch.id,
                source_file_id=source.id,
                source_sha256=source.sha256,
                source_ext=source.file_ext.lower(),
                source_relative_path=relative_path,
                status=status,
                attempt=1,
                error_code=error_code,
                error_message=error_message,
            )
        )
    batch.failed_count = failed_count
    db.flush()
    return batch
