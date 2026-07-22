from __future__ import annotations

from datetime import UTC, datetime

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
