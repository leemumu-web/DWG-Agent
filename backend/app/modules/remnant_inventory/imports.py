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


def _require_item_access(db: Session, item: RemnantImportItem, actor: User) -> RemnantImportBatch:
    batch = db.get(RemnantImportBatch, item.batch_id)
    if batch is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "Import batch not found.")
    if batch.created_by != actor.id and not (_ADMIN_ROLES & user_role_codes(actor)):
        raise AppHTTPException(403, "REMNANT_IMPORT_FORBIDDEN", "Import batch access denied.")
    return batch


def _thickness(value: Decimal | str | int | float) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AppHTTPException(422, "REMNANT_THICKNESS_INVALID", "Thickness is invalid.") from exc
    if not parsed.is_finite() or parsed <= 0 or parsed.as_tuple().exponent < -3:
        raise AppHTTPException(
            422,
            "REMNANT_THICKNESS_INVALID",
            "Thickness must be positive with at most three decimals.",
        )
    return parsed.quantize(Decimal("0.001"))


def _parts(values: Sequence[str]) -> list[str]:
    normalized = [
        unicodedata.normalize("NFKC", value).strip()
        for value in values
        if unicodedata.normalize("NFKC", value).strip()
    ]
    return list(dict.fromkeys(normalized))


def update_import_item(
    db: Session,
    item_id: int,
    *,
    actor: User,
    thickness_mm: Decimal | str | int | float | None = None,
    material_id: int | None = None,
    project_no: str | None = None,
    parts: Sequence[str] | None = None,
) -> RemnantImportItem:
    item = db.get(RemnantImportItem, item_id)
    if item is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_ITEM_NOT_FOUND", "Import item not found.")
    _require_item_access(db, item, actor)
    if item.status != "pending_confirmation":
        raise AppHTTPException(409, "REMNANT_IMPORT_ITEM_LOCKED", "Import item is not editable.")
    if thickness_mm is not None:
        item.corrected_thickness_mm = _thickness(thickness_mm)
    if material_id is not None:
        material = db.get(RemnantMaterial, material_id)
        if material is None or not material.enabled:
            raise AppHTTPException(422, "REMNANT_MATERIAL_INVALID", "Material is not enabled.")
        item.corrected_material_id = material.id
    if project_no is not None:
        item.corrected_project_no = unicodedata.normalize("NFKC", project_no).strip()
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


def retry_import_item(db: Session, item_id: int, *, actor: User) -> ExecutionDispatch:
    item = db.get(RemnantImportItem, item_id)
    if item is None:
        raise AppHTTPException(404, "REMNANT_IMPORT_ITEM_NOT_FOUND", "Import item not found.")
    batch = _require_item_access(db, item, actor)
    if item.status != "failed":
        raise AppHTTPException(409, "REMNANT_IMPORT_RETRY_INVALID", "Only failed items can retry.")
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
        raise AppHTTPException(404, "REMNANT_IMPORT_BATCH_NOT_FOUND", "Import batch not found.")
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
) -> RemnantImportBatch:
    limit = max_files if max_files is not None else settings.remnant_import_max_files
    if not source_files:
        raise AppHTTPException(422, "REMNANT_IMPORT_EMPTY", "At least one drawing is required.")
    if len(source_files) > limit:
        raise AppHTTPException(
            422,
            "REMNANT_IMPORT_TOO_MANY_FILES",
            "Import batch exceeds the configured file limit.",
            {"max_files": limit},
        )

    seen: dict[str, StoredFile] = {}
    for source in source_files:
        extension = source.file_ext.lower()
        if extension not in REMNANT_SOURCE_EXTENSIONS:
            raise AppHTTPException(
                415,
                "REMNANT_FILE_TYPE_NOT_ALLOWED",
                "Only DWG and DXF drawings can be imported.",
            )
        if source.sha256 in seen:
            raise AppHTTPException(
                409,
                "REMNANT_SOURCE_DUPLICATE_IN_BATCH",
                "The same source drawing appears more than once in this batch.",
                {"first_file_id": seen[source.sha256].id, "duplicate_file_id": source.id},
            )
        seen[source.sha256] = source

    existing = db.scalar(select(Remnant).where(Remnant.source_sha256.in_(list(seen))))
    if existing is not None:
        raise AppHTTPException(
            409,
            "REMNANT_SOURCE_DUPLICATE",
            "This source drawing already exists in the remnant inventory.",
            {"remnant_id": existing.id},
        )

    batch = RemnantImportBatch(
        created_by=actor_id,
        status="uploaded",
        total_count=len(source_files),
    )
    db.add(batch)
    db.flush()
    for source in source_files:
        db.add(
            RemnantImportItem(
                batch_id=batch.id,
                source_file_id=source.id,
                source_sha256=source.sha256,
                source_ext=source.file_ext.lower(),
                status="uploaded",
                attempt=1,
            )
        )
    db.flush()
    return batch
