from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.modules.files.interface import StoredFile
from app.modules.identity.interface import Role, User
from app.modules.remnant_inventory.models import (
    Remnant,
    RemnantImportBatch,
    RemnantImportItem,
    RemnantMaterial,
)


def _user(db, name: str, role: str = "remnant_worker") -> User:
    role_row = db.scalar(select(Role).where(Role.code == role))
    if role_row is None:
        role_row = Role(code=role, name=role)
    user = User(
        username=name,
        real_name=name,
        password_hash="x",
        roles=[role_row],
    )
    db.add(user)
    db.flush()
    return user


def _remnant(
    db,
    *,
    owner: User,
    material: RemnantMaterial | None = None,
    thickness: str = "10.000",
    source_ext: str = ".dwg",
    suffix: str = "a",
    status: str = "available",
) -> Remnant:
    material = material or RemnantMaterial(code=f"Q235{suffix.upper()}", family_code="Q235")
    source = StoredFile(
        bucket="dwg-original" if source_ext == ".dwg" else "dxf-original",
        storage_key=f"tests/inventory-{suffix}{source_ext}",
        original_name=f"inventory-{suffix}{source_ext}",
        file_ext=source_ext,
        size_bytes=100,
        sha256=suffix * 64,
        status="available",
        uploaded_by=owner.id,
    )
    dxf = source
    if source_ext == ".dwg":
        dxf = StoredFile(
            bucket="dxf-derived",
            storage_key=f"tests/inventory-{suffix}.dxf",
            original_name=f"inventory-{suffix}.dxf",
            file_ext=".dxf",
            size_bytes=100,
            sha256=(suffix + "d")[:1] * 64,
            status="available",
        )
    batch = RemnantImportBatch(created_by=owner.id, total_count=1, confirmed_count=1)
    db.add_all([material, source, dxf, batch])
    db.flush()
    item = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=source.id,
        dxf_file_id=dxf.id,
        source_sha256=source.sha256,
        source_ext=source_ext,
        status="confirmed",
    )
    db.add(item)
    db.flush()
    row = Remnant(
        import_item_id=item.id,
        source_file_id=source.id,
        dxf_file_id=dxf.id,
        source_sha256=source.sha256,
        thickness_mm=thickness,
        material_id=material.id,
        project_no=f"P-{suffix}",
        status=status,
        imported_by=owner.id,
        confirmed_by=owner.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def test_search_requires_exact_material_and_thickness_and_defaults_to_active(db) -> None:
    from app.modules.remnant_inventory.inventory import search_remnants

    owner = _user(db, "search-owner")
    selected = RemnantMaterial(code="Q235B", family_code="Q235")
    sibling = RemnantMaterial(code="Q235D", family_code="Q235")
    db.add_all([selected, sibling])
    db.flush()
    available = _remnant(db, owner=owner, material=selected, suffix="a")
    reserved = _remnant(db, owner=owner, material=selected, suffix="b", status="reserved")
    _remnant(db, owner=owner, material=sibling, suffix="c")
    _remnant(db, owner=owner, material=selected, suffix="d", status="used")

    page = search_remnants(db, material_id=selected.id, thickness_mm="10", include_family=False)

    assert [row.id for row in page.items] == [available.id, reserved.id]
    assert page.total == 2


def test_family_search_expands_and_history_requires_explicit_status(db) -> None:
    from app.modules.remnant_inventory.inventory import search_remnants

    owner = _user(db, "family-owner")
    first = RemnantMaterial(code="Q235B", family_code="Q235")
    second = RemnantMaterial(code="Q235D", family_code="Q235")
    db.add_all([first, second])
    db.flush()
    one = _remnant(db, owner=owner, material=first, suffix="e")
    two = _remnant(db, owner=owner, material=second, suffix="f")
    used = _remnant(db, owner=owner, material=second, suffix="1", status="used")

    family = search_remnants(db, material_id=first.id, thickness_mm="10", include_family=True)
    history = search_remnants(
        db,
        material_id=first.id,
        thickness_mm="10",
        include_family=True,
        statuses=["used"],
    )
    assert {row.id for row in family.items} == {one.id, two.id}
    assert [row.id for row in history.items] == [used.id]


def test_reservation_is_atomic_and_reports_current_occupant(db) -> None:
    from app.modules.remnant_inventory.inventory import reserve_remnant

    owner = _user(db, "reserve-owner")
    first = _user(db, "reserve-first")
    second = _user(db, "reserve-second")
    row = _remnant(db, owner=owner, suffix="2")

    reserved = reserve_remnant(db, row.id, actor=first, expected_version=1)
    with pytest.raises(HTTPException) as captured:
        reserve_remnant(db, row.id, actor=second, expected_version=1)

    assert reserved.reserved_by == first.id
    assert captured.value.detail["code"] == "REMNANT_ALREADY_RESERVED"
    assert captured.value.detail["details"]["reserved_by"] == first.id


def test_release_then_rereserve_and_mark_used_locks_record(db) -> None:
    from app.modules.remnant_inventory.inventory import (
        mark_remnant_used,
        release_remnant,
        reserve_remnant,
        update_remnant,
    )

    owner = _user(db, "lifecycle-owner")
    worker = _user(db, "lifecycle-worker")
    other = _user(db, "lifecycle-other")
    row = _remnant(db, owner=owner, suffix="3")
    reserve_remnant(db, row.id, actor=worker, expected_version=1)
    with pytest.raises(HTTPException):
        release_remnant(db, row.id, actor=other)
    release_remnant(db, row.id, actor=worker)
    reserve_remnant(db, row.id, actor=other, expected_version=3)
    mark_remnant_used(db, row.id, actor=other)

    with pytest.raises(HTTPException) as captured:
        update_remnant(db, row.id, actor=owner, project_no="changed")
    assert captured.value.detail["code"] == "REMNANT_LOCKED"


def test_only_importer_or_admin_can_edit_or_archive_available_remnant(db) -> None:
    from app.modules.remnant_inventory.inventory import archive_remnant, update_remnant

    owner = _user(db, "edit-owner")
    outsider = _user(db, "edit-outsider")
    admin = _user(db, "edit-admin", "admin")
    row = _remnant(db, owner=owner, suffix="4")

    with pytest.raises(HTTPException):
        update_remnant(db, row.id, actor=outsider, project_no="NO")
    assert update_remnant(db, row.id, actor=owner, project_no="YES").project_no == "YES"
    assert archive_remnant(db, row.id, actor=admin).status == "archived"


def test_optional_inventory_fields_can_be_updated_and_searched_independently(db) -> None:
    from app.modules.remnant_inventory.inventory import list_all_remnants, update_remnant

    owner = _user(db, "metadata-owner")
    row = _remnant(db, owner=owner, suffix="m")

    update_remnant(
        db,
        row.id,
        actor=owner,
        project_no_secondary="合同-M2",
        storage_location="A区-03架",
        remark_1="待复核",
        remark_2="优先使用",
    )

    assert [item.id for item in list_all_remnants(db, project_secondary="合同-M2").items] == [
        row.id
    ]
    assert [item.id for item in list_all_remnants(db, storage_location="03架").items] == [
        row.id
    ]
    assert [item.id for item in list_all_remnants(db, remark_1="复核").items] == [row.id]
    assert [item.id for item in list_all_remnants(db, remark_2="优先").items] == [row.id]

    update_remnant(
        db,
        row.id,
        actor=owner,
        project_no_secondary="",
        storage_location="",
        remark_1="",
        remark_2="",
    )
    assert row.project_no_secondary is None
    assert row.storage_location is None
    assert row.remark_1 is None
    assert row.remark_2 is None


def test_deleting_archived_remnant_keeps_audit_and_frees_source_for_resubmission(db) -> None:
    from app.modules.operations.audit.models import AuditLog
    from app.modules.remnant_inventory.inventory import archive_remnant, delete_archived_remnant

    owner = _user(db, "delete-owner")
    row = _remnant(db, owner=owner, suffix="z")
    source_sha256 = row.source_sha256
    source_file_id = row.source_file_id
    remnant_id = row.id
    archive_remnant(db, row.id, actor=owner)

    delete_archived_remnant(db, row.id, actor=owner)
    db.flush()

    assert db.get(Remnant, remnant_id) is None
    assert db.get(StoredFile, source_file_id) is not None
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "remnants.delete",
            AuditLog.resource_id == remnant_id,
        )
    )
    assert audit is not None
    assert audit.before_json["source_sha256"] == source_sha256

    replacement_item = RemnantImportItem(
        batch_id=row.import_item_id and db.get(RemnantImportItem, row.import_item_id).batch_id,
        source_file_id=source_file_id,
        dxf_file_id=row.dxf_file_id,
        source_sha256=source_sha256,
        source_ext=".dxf",
        status="pending_confirmation",
        corrected_thickness_mm=Decimal("10"),
        corrected_material_id=row.material_id,
        corrected_project_no="再次提交",
        corrected_parts_json=["P-RESUBMIT"],
    )
    db.add(replacement_item)
    db.flush()
    from app.modules.remnant_inventory.imports import confirm_import_items

    result = confirm_import_items(db, [replacement_item.id], actor=owner)
    assert len(result.confirmed) == 1


def test_bulk_archive_partially_succeeds_and_preserves_first_input_order(db) -> None:
    from app.modules.operations.audit.models import AuditLog
    from app.modules.remnant_inventory.inventory import bulk_archive_remnants

    owner = _user(db, "bulk-archive-owner")
    outsider = _user(db, "bulk-archive-outsider")
    own = _remnant(db, owner=owner, suffix="e")
    foreign = _remnant(db, owner=outsider, suffix="f")
    reserved = _remnant(db, owner=owner, suffix="g", status="reserved")

    result = bulk_archive_remnants(
        db,
        [foreign.id, reserved.id, own.id, own.id, 999999],
        actor=owner,
    )
    db.commit()
    db.expire_all()

    assert result.archived == [own.id]
    assert db.get(Remnant, own.id).status == "archived"
    assert [
        (item.remnant_id, item.code, item.message) for item in result.failed
    ] == [
        (foreign.id, "REMNANT_ARCHIVE_FORBIDDEN", "只能归档自己导入的余料。"),
        (reserved.id, "REMNANT_LOCKED", "只有状态为“可用”的余料才能归档。"),
        (999999, "REMNANT_NOT_FOUND", "余料不存在或已被删除。"),
    ]
    audit_rows = list(
        db.scalars(
            select(AuditLog).where(
                AuditLog.action == "remnants.archive",
                AuditLog.resource_id == own.id,
            )
        ).all()
    )
    assert len(audit_rows) == 1


def test_bulk_archive_allows_admin_to_archive_foreign_remnants(db) -> None:
    from app.modules.operations.audit.models import AuditLog
    from app.modules.remnant_inventory.inventory import bulk_archive_remnants

    owner = _user(db, "bulk-archive-foreign-owner")
    admin = _user(db, "bulk-archive-admin", "admin")
    first = _remnant(db, owner=owner, suffix="h")
    second = _remnant(db, owner=owner, suffix="i")

    result = bulk_archive_remnants(db, [second.id, first.id], actor=admin)

    assert result.archived == [second.id, first.id]
    assert result.failed == []
    audit_ids = list(
        db.scalars(
            select(AuditLog.resource_id)
            .where(AuditLog.action == "remnants.archive")
            .order_by(AuditLog.id)
        ).all()
    )
    assert audit_ids == [second.id, first.id]


@pytest.mark.parametrize("source_ext", [".dwg", ".dxf"])
def test_original_download_uses_actual_upload_and_only_reserver_or_admin(
    db, source_ext: str
) -> None:
    from app.modules.remnant_inventory.inventory import (
        build_original_download,
        preview_file_id,
        reserve_remnant,
    )

    owner = _user(db, f"download-owner-{source_ext[1:]}")
    reserver = _user(db, f"download-reserver-{source_ext[1:]}")
    outsider = _user(db, f"download-outsider-{source_ext[1:]}")
    row = _remnant(db, owner=owner, source_ext=source_ext, suffix=source_ext[1])
    reserve_remnant(db, row.id, actor=reserver, expected_version=1)

    assert preview_file_id(db, row.id, actor=outsider) == row.dxf_file_id
    with pytest.raises(HTTPException):
        build_original_download(db, row.id, actor=outsider)
    download = build_original_download(db, row.id, actor=reserver)
    assert download.file_id == row.source_file_id
    assert download.file_name.endswith(source_ext)
    if source_ext == ".dwg":
        assert download.file_id != row.dxf_file_id


def test_generic_file_outlets_honor_remnant_preview_and_original_download_matrix(db) -> None:
    from app.modules.files.access import require_file_read_access
    from app.modules.remnant_inventory.inventory import reserve_remnant

    owner = _user(db, "outlet-owner")
    reserver = _user(db, "outlet-reserver")
    outsider = _user(db, "outlet-outsider")
    row = _remnant(db, owner=owner, source_ext=".dwg", suffix="8")
    source = db.get(StoredFile, row.source_file_id)
    preview = db.get(StoredFile, row.dxf_file_id)

    require_file_read_access(db, outsider, preview, purpose="preview")
    with pytest.raises(HTTPException):
        require_file_read_access(db, outsider, source, purpose="download")

    reserve_remnant(db, row.id, actor=reserver, expected_version=1)
    require_file_read_access(db, reserver, source, purpose="download")
    with pytest.raises(HTTPException):
        require_file_read_access(db, outsider, source, purpose="download")


def test_every_lifecycle_mutation_writes_audit_record(db) -> None:
    from app.modules.operations.audit.models import AuditLog
    from app.modules.remnant_inventory.inventory import release_remnant, reserve_remnant

    owner = _user(db, "audit-owner")
    worker = _user(db, "audit-worker")
    row = _remnant(db, owner=owner, suffix="9")
    reserve_remnant(db, row.id, actor=worker, expected_version=1)
    release_remnant(db, row.id, actor=worker)

    assert [log.action for log in db.query(AuditLog).order_by(AuditLog.id)] == [
        "remnants.reserve",
        "remnants.release",
    ]
