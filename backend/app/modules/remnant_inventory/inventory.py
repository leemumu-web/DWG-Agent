from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import HTTPException
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
ALL_STATUS_ORDER = ("available", "reserved", "used", "archived")
GLOBAL_SORTS = {
    "created_asc",
    "created_desc",
    "status",
    "thickness_asc",
    "thickness_desc",
}
_ADMIN_ROLES = {ROLE_ADMIN, ROLE_SUPER_ADMIN}
_UNSET = object()


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


@dataclass(frozen=True)
class BulkArchiveFailure:
    remnant_id: int
    code: str
    message: str


@dataclass(frozen=True)
class BulkArchiveResult:
    archived: list[int]
    failed: list[BulkArchiveFailure]


def _require_user(actor: User) -> None:
    if not can_use_remnants(actor):
        raise AppHTTPException(403, "REMNANT_FORBIDDEN", "当前账号无权使用余料库。")


def _get(db: Session, remnant_id: int) -> Remnant:
    row = db.get(Remnant, remnant_id)
    if row is None:
        raise AppHTTPException(404, "REMNANT_NOT_FOUND", "余料不存在或已被删除。")
    return row


def _get_for_update(db: Session, remnant_id: int) -> Remnant:
    row = db.scalar(
        select(Remnant)
        .where(Remnant.id == remnant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise AppHTTPException(404, "REMNANT_NOT_FOUND", "余料不存在或已被删除。")
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
        raise AppHTTPException(422, "REMNANT_PAGE_INVALID", "分页参数不正确。")
    selected_statuses = tuple(dict.fromkeys(statuses or ACTIVE_STATUSES))
    if not selected_statuses or not set(selected_statuses) <= ALL_STATUSES:
        raise AppHTTPException(422, "REMNANT_STATUS_INVALID", "余料状态不正确。")
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


def list_all_remnants(
    db: Session,
    *,
    material_id: int | None = None,
    thickness_mm: Decimal | str | int | float | None = None,
    statuses: Sequence[str] | None = None,
    project: str | None = None,
    project_secondary: str | None = None,
    storage_location: str | None = None,
    remark_1: str | None = None,
    remark_2: str | None = None,
    part: str | None = None,
    sort: str = "created_desc",
    page: int = 1,
    page_size: int = 50,
) -> RemnantPage:
    if page < 1 or page_size < 1 or page_size > 200:
        raise AppHTTPException(422, "REMNANT_PAGE_INVALID", "分页参数不正确。")
    selected_statuses = tuple(dict.fromkeys(statuses or ALL_STATUS_ORDER))
    if not selected_statuses or not set(selected_statuses) <= ALL_STATUSES:
        raise AppHTTPException(422, "REMNANT_STATUS_INVALID", "余料状态不正确。")
    if sort not in GLOBAL_SORTS:
        raise AppHTTPException(422, "REMNANT_SORT_INVALID", "余料排序方式不正确。")

    filters = [Remnant.status.in_(selected_statuses)]
    if material_id is not None:
        filters.append(Remnant.material_id == material_id)
    if thickness_mm is not None:
        filters.append(Remnant.thickness_mm == _thickness(thickness_mm))
    if normalized_project := (project or "").strip():
        filters.append(Remnant.project_no.ilike(f"%{normalized_project}%"))
    for value, column in (
        (project_secondary, Remnant.project_no_secondary),
        (storage_location, Remnant.storage_location),
        (remark_1, Remnant.remark_1),
        (remark_2, Remnant.remark_2),
    ):
        if normalized := (value or "").strip():
            filters.append(column.ilike(f"%{normalized}%"))
    if normalized_part := (part or "").strip():
        filters.append(
            Remnant.id.in_(
                select(RemnantPart.remnant_id).where(
                    RemnantPart.part_no.ilike(f"%{normalized_part}%")
                )
            )
        )

    status_order = case(
        (Remnant.status == "available", 0),
        (Remnant.status == "reserved", 1),
        (Remnant.status == "used", 2),
        else_=3,
    )
    ordering = {
        "created_asc": (Remnant.created_at.asc(), Remnant.id.asc()),
        "created_desc": (Remnant.created_at.desc(), Remnant.id.desc()),
        "status": (status_order, Remnant.created_at.desc(), Remnant.id.desc()),
        "thickness_asc": (Remnant.thickness_mm.asc(), Remnant.id.asc()),
        "thickness_desc": (Remnant.thickness_mm.desc(), Remnant.id.desc()),
    }[sort]
    total = db.scalar(select(func.count(Remnant.id)).where(*filters)) or 0
    rows = list(
        db.scalars(
            select(Remnant)
            .where(*filters)
            .order_by(*ordering)
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
    if changed != 1:
        # MySQL's default REPEATABLE READ may keep a stale snapshot after a
        # competing conditional UPDATE wins. A locking read is a current read,
        # so it reports the committed reserver instead of a generic conflict.
        row = db.scalar(
            select(Remnant)
            .where(Remnant.id == remnant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AppHTTPException(404, "REMNANT_NOT_FOUND", "余料不存在或已被删除。")
        if row.status == "reserved":
            raise AppHTTPException(
                409,
                "REMNANT_ALREADY_RESERVED",
                "该余料已被预留。",
                {"reserved_by": row.reserved_by},
            )
        raise AppHTTPException(409, "REMNANT_STATE_CONFLICT", "余料状态已变更，请刷新后重试。")
    row = _get(db, remnant_id)
    db.refresh(row)
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
    row = _get_for_update(db, remnant_id)
    if row.status != "reserved":
        raise AppHTTPException(409, "REMNANT_NOT_RESERVED", "该余料当前未被预留。")
    if row.reserved_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403, "REMNANT_RESERVATION_FORBIDDEN", "该余料由其他工人预留，无权操作。"
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
    row = _get_for_update(db, remnant_id)
    if row.status != "reserved":
        raise AppHTTPException(409, "REMNANT_NOT_RESERVED", "该余料当前未被预留。")
    if row.reserved_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403, "REMNANT_RESERVATION_FORBIDDEN", "该余料由其他工人预留，无权操作。"
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
    project_no_secondary: str | None | object = _UNSET,
    storage_location: str | None | object = _UNSET,
    remark_1: str | None | object = _UNSET,
    remark_2: str | None | object = _UNSET,
    parts: Sequence[str] | None = None,
) -> Remnant:
    _require_user(actor)
    row = _get_for_update(db, remnant_id)
    if row.status != "available":
        raise AppHTTPException(409, "REMNANT_LOCKED", "只有状态为“可用”的余料才能编辑。")
    if row.imported_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403, "REMNANT_EDIT_FORBIDDEN", "只能编辑自己导入的余料。"
        )
    before = {
        "thickness_mm": str(row.thickness_mm),
        "material_id": row.material_id,
        "project_no": row.project_no,
        "project_no_secondary": row.project_no_secondary,
        "storage_location": row.storage_location,
        "remark_1": row.remark_1,
        "remark_2": row.remark_2,
        "version": row.version,
    }
    if thickness_mm is not None:
        row.thickness_mm = _thickness(thickness_mm)
    if material_id is not None:
        material = db.get(RemnantMaterial, material_id)
        if material is None or not material.enabled:
            raise AppHTTPException(422, "REMNANT_MATERIAL_INVALID", "请选择已启用的材质。")
        row.material_id = material.id
    if project_no is not None:
        normalized = project_no.strip()
        if not normalized:
            raise AppHTTPException(422, "REMNANT_PROJECT_REQUIRED", "请填写项目编号。")
        row.project_no = normalized
    for value, attribute in (
        (project_no_secondary, "project_no_secondary"),
        (storage_location, "storage_location"),
        (remark_1, "remark_1"),
        (remark_2, "remark_2"),
    ):
        if value is not _UNSET:
            normalized = str(value or "").strip()
            setattr(row, attribute, normalized or None)
    if parts is not None:
        normalized_parts = _parts(parts)
        if not normalized_parts:
            raise AppHTTPException(422, "REMNANT_PARTS_REQUIRED", "至少填写一个零件编号。")
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
            "project_no_secondary": row.project_no_secondary,
            "storage_location": row.storage_location,
            "remark_1": row.remark_1,
            "remark_2": row.remark_2,
            "version": row.version,
        },
    )
    return row


def archive_remnant(db: Session, remnant_id: int, *, actor: User) -> Remnant:
    _require_user(actor)
    row = _get_for_update(db, remnant_id)
    if row.status != "available":
        raise AppHTTPException(
            409,
            "REMNANT_LOCKED",
            "只有状态为“可用”的余料才能归档。",
        )
    if row.imported_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403,
            "REMNANT_ARCHIVE_FORBIDDEN",
            "只能归档自己导入的余料。",
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


def delete_archived_remnant(db: Session, remnant_id: int, *, actor: User) -> None:
    """Hard-delete only an archived inventory row; source files and audit ledger remain."""
    _require_user(actor)
    row = _get_for_update(db, remnant_id)
    if row.status != "archived":
        raise AppHTTPException(
            409,
            "REMNANT_DELETE_REQUIRES_ARCHIVED",
            "只有已归档的余料才能删除。",
        )
    if row.imported_by != actor.id and not _is_admin(actor):
        raise AppHTTPException(
            403,
            "REMNANT_DELETE_FORBIDDEN",
            "只能删除自己导入且已归档的余料。",
        )
    before = {
        "source_sha256": row.source_sha256,
        "source_file_id": row.source_file_id,
        "dxf_file_id": row.dxf_file_id,
        "material_id": row.material_id,
        "thickness_mm": str(row.thickness_mm),
        "project_no": row.project_no,
        "project_no_secondary": row.project_no_secondary,
        "storage_location": row.storage_location,
        "remark_1": row.remark_1,
        "remark_2": row.remark_2,
        "status": row.status,
    }
    _audit(db, actor=actor, action="delete", row=row, before=before, after={"deleted": True})
    db.delete(row)
    db.flush()


def bulk_archive_remnants(
    db: Session,
    remnant_ids: Sequence[int],
    *,
    actor: User,
) -> BulkArchiveResult:
    _require_user(actor)
    result = BulkArchiveResult(archived=[], failed=[])
    for remnant_id in dict.fromkeys(remnant_ids):
        try:
            with db.begin_nested():
                archive_remnant(db, remnant_id, actor=actor)
            result.archived.append(remnant_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            result.failed.append(
                BulkArchiveFailure(
                    remnant_id=remnant_id,
                    code=str(detail.get("code", "REMNANT_ARCHIVE_FAILED")),
                    message=str(detail.get("message", "余料归档失败。")),
                )
            )
    return result


def preview_file_id(db: Session, remnant_id: int, *, actor: User) -> int:
    _require_user(actor)
    return _get(db, remnant_id).dxf_file_id


def build_original_download(db: Session, remnant_id: int, *, actor: User) -> OriginalDownload:
    _require_user(actor)
    row = _get(db, remnant_id)
    if row.status != "reserved" or (row.reserved_by != actor.id and not _is_admin(actor)):
        raise AppHTTPException(
            403, "REMNANT_DOWNLOAD_FORBIDDEN", "只有当前预留人或管理员可以下载原图。"
        )
    source = db.get(StoredFile, row.source_file_id)
    if source is None or source.status == "deleted":
        raise AppHTTPException(404, "REMNANT_SOURCE_NOT_FOUND", "原始图纸不存在或已被删除。")
    signed = build_signed_download_url(source.id)
    return OriginalDownload(
        file_id=source.id,
        file_name=source.original_name,
        file_ext=source.file_ext,
        url=signed.url,
        expires_in=signed.expires_in,
    )
