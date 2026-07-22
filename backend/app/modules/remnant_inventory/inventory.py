from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import Session

from app.modules.files.interface import StoredFile, build_signed_download_url
from app.modules.identity.interface import User, user_role_codes
from app.modules.operations.audit.interface import write_audit_log
from app.modules.remnant_inventory.access import can_use_remnants
from app.modules.remnant_inventory.imports import _parts, _thickness
from app.modules.remnant_inventory.materials import material_ids_for_search
from app.modules.remnant_inventory.models import Remnant, RemnantMaterial, RemnantPart
from app.platform.config.constants import ROLE_ADMIN, ROLE_SUPER_ADMIN
from app.platform.http.exceptions import AppHTTPException

ACTIVE_STATUSES = ("available", "reserved")
ALL_STATUSES = {"available", "reserved", "used", "archived"}
_ADMIN_ROLES = {ROLE_ADMIN, ROLE_SUPER_ADMIN}


@dataclass(frozen=True)
class RemnantPage:
    items: list[Remnant]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class OriginalDownload:
    file_id: int
    file_name: str
    file_ext: str
    url: str
    expires_in: int


def _require_user(actor: User) -> None:
    if not can_use_remnants(actor):
        raise AppHTTPException(403, "REMNANT_FORBIDDEN", "Remnant inventory access denied.")


def _get(db: Session, remnant_id: int) -> Remnant:
    row = db.get(Remnant, remnant_id)
    if row is None:
        raise AppHTTPException(404, "REMNANT_NOT_FOUND", "Remnant not found.")
    return row


def _is_admin(actor: User) -> bool:
    return bool(_ADMIN_ROLES & user_role_codes(actor))


def search_remnants(
    db: Session,
    *,
    material_id: int,
    thickness_mm: Decimal | str | int | float,
    include_family: bool = False,
    statuses: Sequence[str] | None = None,
    page: int = 1,
    page_size: int = 50,
) -> RemnantPage:
    if page < 1 or page_size < 1 or page_size > 200:
        raise AppHTTPException(422, "REMNANT_PAGE_INVALID", "Pagination is invalid.")
    selected_statuses = tuple(dict.fromkeys(statuses or ACTIVE_STATUSES))
    if not selected_statuses or not set(selected_statuses) <= ALL_STATUSES:
        raise AppHTTPException(422, "REMNANT_STATUS_INVALID", "Remnant status is invalid.")
    material_ids = material_ids_for_search(db, material_id, include_family=include_family)
    thickness = _thickness(thickness_mm)
    filters = (
        Remnant.material_id.in_(material_ids),
        Remnant.thickness_mm == thickness,
        Remnant.status.in_(selected_statuses),
    )
    total = db.scalar(select(func.count(Remnant.id)).where(*filters)) or 0
    status_order = case(
        (Remnant.status == "available", 0), (Remnant.status == "reserved", 1), else_=2
    )
    rows = list(
        db.scalars(
            select(Remnant)
            .where(*filters)
            .order_by(status_order, Remnant.created_at.desc(), Remnant.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return RemnantPage(rows, int(total), page, page_size)


def _audit(
    db: Session,
    *,
    actor: User,
    action: str,
    row: Remnant,
    before: dict,
    after: dict,
) -> None:
    write_audit_log(
        db,
        actor_user_id=actor.id,
        action=f"remnants.{action}",
        resource_type="remnant",
        resource_id=row.id,
        before_json=before,
        after_json=after,
    )


def reserve_remnant(db: Session, remnant_id: int, *, actor: User, expected_version: int) -> Remnant:
    _require_user(actor)
    changed = db.execute(
        update(Remnant)
        .where(
            Remnant.id == remnant_id,
            Remnant.status == "available",
            Remnant.version == expected_version,
        )
        .values(
            status="reserved",
            reserved_by=actor.id,
            reserved_at=datetime.now(UTC),
            version=Remnant.version + 1,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    row = _get(db, remnant_id)
    db.refresh(row)
    if changed != 1:
        if row.status == "reserved":
            raise AppHTTPException(
                409,
                "REMNANT_ALREADY_RESERVED",
                "Remnant is already reserved.",
                {"reserved_by": row.reserved_by},
            )
        raise AppHTTPException(409, "REMNANT_STATE_CONFLICT", "Remnant state changed.")
    _audit(
        db,
        actor=actor,
        action="reserve",
        row=row,
        before={"status": "available", "version": expected_version},
        after={"status": row.status, "reserved_by": actor.id, "version": row.version},
    )
    return row


def release_remnant(db: Session, remnant_id: int, *, actor: User) -> Remnant:
    _require_user(actor)
    row = _get(db, remnant_id)
    if row.status != "reserved":
        raise AppHTTPException(409, "REMNANT_NOT_RESERVED", "Remnant is not reserved.")
    if row.reserved_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403, "REMNANT_RESERVATION_FORBIDDEN", "Reservation belongs to another user."
        )
    before = {"status": row.status, "reserved_by": row.reserved_by, "version": row.version}
    row.status = "available"
    row.reserved_by = None
    row.reserved_at = None
    row.version += 1
    db.flush()
    _audit(
        db,
        actor=actor,
        action="release",
        row=row,
        before=before,
        after={"status": row.status, "version": row.version},
    )
    return row


def mark_remnant_used(db: Session, remnant_id: int, *, actor: User) -> Remnant:
    _require_user(actor)
    row = _get(db, remnant_id)
    if row.status != "reserved":
        raise AppHTTPException(409, "REMNANT_NOT_RESERVED", "Remnant is not reserved.")
    if row.reserved_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403, "REMNANT_RESERVATION_FORBIDDEN", "Reservation belongs to another user."
        )
    before = {"status": row.status, "reserved_by": row.reserved_by, "version": row.version}
    row.status = "used"
    row.used_by = actor.id
    row.used_at = datetime.now(UTC)
    row.version += 1
    db.flush()
    _audit(
        db,
        actor=actor,
        action="use",
        row=row,
        before=before,
        after={"status": row.status, "used_by": actor.id, "version": row.version},
    )
    return row


def update_remnant(
    db: Session,
    remnant_id: int,
    *,
    actor: User,
    thickness_mm: Decimal | str | int | float | None = None,
    material_id: int | None = None,
    project_no: str | None = None,
    parts: Sequence[str] | None = None,
) -> Remnant:
    _require_user(actor)
    row = _get(db, remnant_id)
    if row.status != "available":
        raise AppHTTPException(409, "REMNANT_LOCKED", "Only available remnants are editable.")
    if row.imported_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403, "REMNANT_EDIT_FORBIDDEN", "Only importer or administrator can edit."
        )
    before = {
        "thickness_mm": str(row.thickness_mm),
        "material_id": row.material_id,
        "project_no": row.project_no,
        "version": row.version,
    }
    if thickness_mm is not None:
        row.thickness_mm = _thickness(thickness_mm)
    if material_id is not None:
        material = db.get(RemnantMaterial, material_id)
        if material is None or not material.enabled:
            raise AppHTTPException(422, "REMNANT_MATERIAL_INVALID", "Material is not enabled.")
        row.material_id = material.id
    if project_no is not None:
        normalized = project_no.strip()
        if not normalized:
            raise AppHTTPException(422, "REMNANT_PROJECT_REQUIRED", "Project number is required.")
        row.project_no = normalized
    if parts is not None:
        normalized_parts = _parts(parts)
        if not normalized_parts:
            raise AppHTTPException(422, "REMNANT_PARTS_REQUIRED", "At least one part is required.")
        db.query(RemnantPart).filter(RemnantPart.remnant_id == row.id).delete()
        db.add_all([RemnantPart(remnant_id=row.id, part_no=value) for value in normalized_parts])
    row.version += 1
    db.flush()
    _audit(
        db,
        actor=actor,
        action="update",
        row=row,
        before=before,
        after={
            "thickness_mm": str(row.thickness_mm),
            "material_id": row.material_id,
            "project_no": row.project_no,
            "version": row.version,
        },
    )
    return row


def archive_remnant(db: Session, remnant_id: int, *, actor: User) -> Remnant:
    _require_user(actor)
    row = _get(db, remnant_id)
    if row.status != "available":
        raise AppHTTPException(409, "REMNANT_LOCKED", "Only available remnants can be archived.")
    if row.imported_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403, "REMNANT_ARCHIVE_FORBIDDEN", "Only importer or administrator can archive."
        )
    before = {"status": row.status, "version": row.version}
    row.status = "archived"
    row.archived_by = actor.id
    row.archived_at = datetime.now(UTC)
    row.version += 1
    db.flush()
    _audit(
        db,
        actor=actor,
        action="archive",
        row=row,
        before=before,
        after={"status": row.status, "version": row.version},
    )
    return row


def preview_file_id(db: Session, remnant_id: int, *, actor: User) -> int:
    _require_user(actor)
    return _get(db, remnant_id).dxf_file_id


def build_original_download(db: Session, remnant_id: int, *, actor: User) -> OriginalDownload:
    _require_user(actor)
    row = _get(db, remnant_id)
    if row.status != "reserved" or (row.reserved_by != actor.id and not _is_admin(actor)):
        raise AppHTTPException(
            403, "REMNANT_DOWNLOAD_FORBIDDEN", "Original drawing download denied."
        )
    source = db.get(StoredFile, row.source_file_id)
    if source is None or source.status == "deleted":
        raise AppHTTPException(404, "REMNANT_SOURCE_NOT_FOUND", "Original drawing not found.")
    signed = build_signed_download_url(source.id)
    return OriginalDownload(
        file_id=source.id,
        file_name=source.original_name,
        file_ext=source.file_ext,
        url=signed.url,
        expires_in=signed.expires_in,
    )
