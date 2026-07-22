from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.modules.files.interface import StoredFile
from app.modules.identity.interface import User
from app.modules.remnant_inventory.models import (
    Remnant,
    RemnantImportBatch,
    RemnantImportItem,
    RemnantMaterial,
)


def _user(db) -> User:
    user = User(username="remnant-importer", real_name="余料工", password_hash="x")
    db.add(user)
    db.flush()
    return user


def _file(db, *, name: str, sha: str, ext: str) -> StoredFile:
    row = StoredFile(
        bucket="dwg-original" if ext == ".dwg" else "dxf-original",
        storage_key=f"tests/{name}-{sha[:8]}",
        original_name=name,
        file_ext=ext,
        size_bytes=2048,
        sha256=sha,
        status="available",
    )
    db.add(row)
    db.flush()
    return row


def test_dxf_structure_validator_accepts_sections_and_rejects_renamed_bytes() -> None:
    from app.modules.files.interface import validate_dxf_structure

    validate_dxf_structure(b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n")
    with pytest.raises(HTTPException) as captured:
        validate_dxf_structure(b"this is not a drawing")
    assert captured.value.status_code == 415
    assert captured.value.detail["code"] == "FILE_NOT_DXF"


def test_registers_mixed_dwg_dxf_batch_with_independent_items(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    dwg = _file(db, name="a.dwg", sha="a" * 64, ext=".dwg")
    dxf = _file(db, name="b.dxf", sha="b" * 64, ext=".dxf")

    batch = register_import_batch(db, actor_id=actor.id, source_files=[dwg, dxf])

    items = (
        db.query(RemnantImportItem)
        .filter_by(batch_id=batch.id)
        .order_by(RemnantImportItem.id)
        .all()
    )
    assert batch.total_count == 2
    assert [
        (item.source_file_id, item.source_ext, item.status, item.attempt) for item in items
    ] == [
        (dwg.id, ".dwg", "uploaded", 1),
        (dxf.id, ".dxf", "uploaded", 1),
    ]


@pytest.mark.parametrize("count", [0, 3])
def test_batch_enforces_non_empty_configured_file_limit(db, count: int) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    files = [
        _file(db, name=f"{index}.dxf", sha=f"{index:064x}", ext=".dxf") for index in range(count)
    ]
    with pytest.raises(HTTPException) as captured:
        register_import_batch(db, actor_id=actor.id, source_files=files, max_files=2)
    assert captured.value.detail["code"] in {
        "REMNANT_IMPORT_EMPTY",
        "REMNANT_IMPORT_TOO_MANY_FILES",
    }


def test_configured_limit_allows_backpressure_batch_larger_than_twenty(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    files = [
        _file(db, name=f"backpressure-{index}.dxf", sha=f"{index + 100:064x}", ext=".dxf")
        for index in range(21)
    ]

    batch = register_import_batch(db, actor_id=actor.id, source_files=files, max_files=100)

    assert batch.total_count == 21
    assert db.query(RemnantImportItem).filter_by(batch_id=batch.id).count() == 21


def test_rejects_zip_even_if_it_is_already_in_file_registry(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    source = _file(db, name="drawings.zip", sha="c" * 64, ext=".zip")
    with pytest.raises(HTTPException) as captured:
        register_import_batch(db, actor_id=actor.id, source_files=[source])
    assert captured.value.detail["code"] == "REMNANT_FILE_TYPE_NOT_ALLOWED"


def test_same_batch_sha_is_rejected_before_creating_ledger(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    first = _file(db, name="one.dxf", sha="d" * 64, ext=".dxf")
    second = _file(db, name="two.dxf", sha="d" * 64, ext=".dxf")
    with pytest.raises(HTTPException) as captured:
        register_import_batch(db, actor_id=actor.id, source_files=[first, second])
    assert captured.value.detail["code"] == "REMNANT_SOURCE_DUPLICATE_IN_BATCH"
    assert db.query(RemnantImportBatch).count() == 0


def test_formal_inventory_sha_returns_existing_remnant_id(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    source = _file(db, name="existing.dxf", sha="e" * 64, ext=".dxf")
    material = RemnantMaterial(code="Q235B", family_code="Q235")
    old_batch = RemnantImportBatch(created_by=actor.id, total_count=1)
    db.add_all([material, old_batch])
    db.flush()
    old_item = RemnantImportItem(
        batch_id=old_batch.id,
        source_file_id=source.id,
        dxf_file_id=source.id,
        source_sha256=source.sha256,
        source_ext=".dxf",
        status="confirmed",
    )
    db.add(old_item)
    db.flush()
    existing = Remnant(
        import_item_id=old_item.id,
        source_file_id=source.id,
        dxf_file_id=source.id,
        source_sha256=source.sha256,
        thickness_mm="10.000",
        material_id=material.id,
        project_no="P1",
        imported_by=actor.id,
        confirmed_by=actor.id,
        confirmed_at=datetime.now(UTC),
    )
    db.add(existing)
    db.flush()
    duplicate_upload = _file(db, name="copy.dxf", sha=source.sha256, ext=".dxf")

    with pytest.raises(HTTPException) as captured:
        register_import_batch(db, actor_id=actor.id, source_files=[duplicate_upload])
    assert captured.value.detail["code"] == "REMNANT_SOURCE_DUPLICATE"
    assert captured.value.detail["details"]["remnant_id"] == existing.id


def test_remnant_settings_are_safe_and_configurable() -> None:
    from app.platform.config.settings import Settings

    defaults = Settings(_env_file=None)
    assert defaults.remnant_inventory_enabled is False
    assert defaults.remnant_import_max_files == 100
    configured = Settings(
        _env_file=None, remnant_import_max_files=25, remnant_inventory_enabled=True
    )
    assert configured.remnant_import_max_files == 25
    assert configured.remnant_inventory_enabled is True
