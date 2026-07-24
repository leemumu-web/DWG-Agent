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
    assert batch.import_mode == "manual"


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


def test_auto_batch_normalizes_paths_and_keeps_duplicate_rows_failed(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    first = _file(db, name="one.dxf", sha="f" * 64, ext=".dxf")
    duplicate = _file(db, name="two.dxf", sha="f" * 64, ext=".dxf")
    unique = _file(db, name="three.dwg", sha="1" * 64, ext=".dwg")

    batch = register_import_batch(
        db,
        actor_id=actor.id,
        source_files=[first, duplicate, unique],
        import_mode="auto",
        default_project_no="  PRJ-001  ",
        source_folder_name="  来料批次  ",
        source_relative_paths=[
            r"一层\one.dxf",
            r"一层\副本\two.dxf",
            "二层/three.dwg",
        ],
    )

    items = (
        db.query(RemnantImportItem)
        .filter_by(batch_id=batch.id)
        .order_by(RemnantImportItem.id)
        .all()
    )
    assert (batch.import_mode, batch.default_project_no, batch.source_folder_name) == (
        "auto",
        "PRJ-001",
        "来料批次",
    )
    assert [item.source_relative_path for item in items] == [
        "一层/one.dxf",
        "一层/副本/two.dxf",
        "二层/three.dwg",
    ]
    assert [item.status for item in items] == ["uploaded", "failed", "uploaded"]
    assert items[1].error_code == "REMNANT_SOURCE_DUPLICATE_IN_BATCH"
    assert items[1].error_message == "同一张源图纸在本批次中重复，已跳过该文件。"
    assert batch.failed_count == 1


def test_auto_batch_marks_formal_inventory_duplicate_and_keeps_other_rows(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    source = _file(db, name="formal.dxf", sha="7" * 64, ext=".dxf")
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
    db.add(
        Remnant(
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
    )
    db.flush()
    duplicate = _file(db, name="formal-copy.dxf", sha=source.sha256, ext=".dxf")
    unique = _file(db, name="unique.dxf", sha="8" * 64, ext=".dxf")

    batch = register_import_batch(
        db,
        actor_id=actor.id,
        source_files=[duplicate, unique],
        import_mode="auto",
        default_project_no="P2",
        source_relative_paths=["formal-copy.dxf", "unique.dxf"],
    )

    items = (
        db.query(RemnantImportItem)
        .filter_by(batch_id=batch.id)
        .order_by(RemnantImportItem.id)
        .all()
    )
    assert [item.status for item in items] == ["failed", "uploaded"]
    assert items[0].error_code == "REMNANT_SOURCE_DUPLICATE"
    assert batch.failed_count == 1


@pytest.mark.parametrize(
    "relative_path",
    ["", "   ", "../escape.dxf", "nested/../../escape.dxf", "/absolute.dxf", r"C:\x.dxf"],
)
def test_auto_batch_rejects_unsafe_relative_paths(db, relative_path: str) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    source = _file(db, name="unsafe.dxf", sha="2" * 64, ext=".dxf")
    with pytest.raises(HTTPException) as captured:
        register_import_batch(
            db,
            actor_id=actor.id,
            source_files=[source],
            import_mode="auto",
            default_project_no="P1",
            source_relative_paths=[relative_path],
        )
    assert captured.value.detail["code"] == "REMNANT_SOURCE_PATH_INVALID"


def test_auto_batch_requires_explicit_project(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    source = _file(db, name="project-required.dxf", sha="3" * 64, ext=".dxf")
    with pytest.raises(HTTPException) as captured:
        register_import_batch(
            db,
            actor_id=actor.id,
            source_files=[source],
            import_mode="auto",
            default_project_no=" ",
            source_relative_paths=["project-required.dxf"],
        )
    assert captured.value.detail["code"] == "REMNANT_PROJECT_REQUIRED"


def test_auto_batch_requires_one_path_per_file(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    source = _file(db, name="aligned.dxf", sha="9" * 64, ext=".dxf")
    with pytest.raises(HTTPException) as captured:
        register_import_batch(
            db,
            actor_id=actor.id,
            source_files=[source],
            import_mode="auto",
            default_project_no="P1",
            source_relative_paths=[],
        )
    assert captured.value.detail["code"] == "REMNANT_SOURCE_PATH_COUNT_MISMATCH"


def test_auto_batch_accepts_source_metadata_at_storage_limits(db) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    source = _file(db, name="limit.dxf", sha="b" * 64, ext=".dxf")
    relative_path = f"{'a' * 1020}.dxf"
    folder_name = "根" * 255

    batch = register_import_batch(
        db,
        actor_id=actor.id,
        source_files=[source],
        import_mode="auto",
        default_project_no="P1",
        source_folder_name=folder_name,
        source_relative_paths=[relative_path],
    )
    item = db.query(RemnantImportItem).filter_by(batch_id=batch.id).one()

    assert len(item.source_relative_path) == 1024
    assert len(batch.source_folder_name) == 255


@pytest.mark.parametrize(
    ("folder_name", "relative_path", "code", "message"),
    [
        (
            None,
            f"{'a' * 1021}.dxf",
            "REMNANT_SOURCE_PATH_TOO_LONG",
            "图纸相对路径不能超过 1024 个字符。",
        ),
        (
            "根" * 256,
            "one.dxf",
            "REMNANT_SOURCE_FOLDER_TOO_LONG",
            "来源文件夹名称不能超过 255 个字符。",
        ),
    ],
)
def test_auto_batch_rejects_source_metadata_over_storage_limits(
    db,
    folder_name: str | None,
    relative_path: str,
    code: str,
    message: str,
) -> None:
    from app.modules.remnant_inventory.imports import register_import_batch

    actor = _user(db)
    source = _file(db, name="too-long.dxf", sha="c" * 64, ext=".dxf")
    with pytest.raises(HTTPException) as captured:
        register_import_batch(
            db,
            actor_id=actor.id,
            source_files=[source],
            import_mode="auto",
            default_project_no="P1",
            source_folder_name=folder_name,
            source_relative_paths=[relative_path],
        )

    assert captured.value.status_code == 422
    assert captured.value.detail["code"] == code
    assert captured.value.detail["message"] == message


def test_bulk_project_and_single_item_cancel_update_only_owned_nonterminal_rows(db) -> None:
    from app.modules.remnant_inventory.imports import (
        bulk_apply_project,
        cancel_import_item,
    )

    actor = _user(db)
    first = _file(db, name="project-a.dxf", sha="4" * 64, ext=".dxf")
    second = _file(db, name="project-b.dxf", sha="5" * 64, ext=".dxf")
    batch = RemnantImportBatch(
        created_by=actor.id,
        import_mode="auto",
        default_project_no="OLD",
        total_count=2,
    )
    db.add(batch)
    db.flush()
    pending = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=first.id,
        source_sha256=first.sha256,
        source_ext=".dxf",
        status="pending_confirmation",
    )
    confirmed = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=second.id,
        source_sha256=second.sha256,
        source_ext=".dxf",
        status="confirmed",
    )
    db.add_all([pending, confirmed])
    db.flush()

    changed = bulk_apply_project(
        db,
        batch.id,
        item_ids=[pending.id, confirmed.id],
        project_no="  NEW-PROJECT  ",
        actor=actor,
    )
    cancelled = cancel_import_item(db, pending.id, actor=actor)

    assert changed == [pending.id]
    assert pending.corrected_project_no == "NEW-PROJECT"
    assert confirmed.corrected_project_no is None
    assert cancelled.status == "cancelled"
    assert db.get(RemnantImportItem, pending.id) is pending
    assert db.get(StoredFile, first.id).status == "available"
    assert batch.cancelled_count == 1


def test_bulk_optional_metadata_updates_only_supplied_fields_and_allows_clear(db) -> None:
    from app.modules.remnant_inventory.imports import bulk_apply_optional_metadata

    actor = _user(db)
    source = _file(db, name="optional-metadata.dxf", sha="7" * 64, ext=".dxf")
    batch = RemnantImportBatch(created_by=actor.id, total_count=1)
    db.add(batch)
    db.flush()
    item = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=source.id,
        source_sha256=source.sha256,
        source_ext=".dxf",
        status="pending_confirmation",
        corrected_project_no_secondary="合同-02",
        corrected_storage_location="A区-03架",
    )
    db.add(item)
    db.flush()

    changed = bulk_apply_optional_metadata(
        db,
        batch.id,
        item_ids=[item.id],
        actor=actor,
        project_no_secondary=None,
    )

    assert changed == [item.id]
    assert item.corrected_project_no_secondary is None
    assert item.corrected_storage_location == "A区-03架"


def test_bulk_optional_metadata_rejects_request_without_update_fields(db) -> None:
    from app.modules.remnant_inventory.imports import bulk_apply_optional_metadata

    actor = _user(db)
    batch = RemnantImportBatch(created_by=actor.id, total_count=0)
    db.add(batch)
    db.flush()

    with pytest.raises(HTTPException) as captured:
        bulk_apply_optional_metadata(db, batch.id, item_ids=[1], actor=actor)

    assert captured.value.status_code == 422
    assert captured.value.detail == {
        "code": "REMNANT_OPTIONAL_METADATA_REQUIRED",
        "message": "请至少选择一项需要批量更新的附加信息。",
        "details": {},
    }


def test_permanent_duplicate_failure_cannot_be_retried(db) -> None:
    from app.modules.remnant_inventory.imports import retry_import_item

    actor = _user(db)
    source = _file(db, name="duplicate.dxf", sha="6" * 64, ext=".dxf")
    batch = RemnantImportBatch(created_by=actor.id, total_count=1)
    db.add(batch)
    db.flush()
    item = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=source.id,
        source_sha256=source.sha256,
        source_ext=".dxf",
        status="failed",
        error_code="REMNANT_SOURCE_DUPLICATE_IN_BATCH",
    )
    db.add(item)
    db.flush()

    with pytest.raises(HTTPException) as captured:
        retry_import_item(db, item.id, actor=actor)
    assert captured.value.detail["code"] == "REMNANT_IMPORT_RETRY_INVALID"


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
