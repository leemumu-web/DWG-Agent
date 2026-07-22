from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.modules.files.interface import StoredFile
from app.modules.identity.interface import Role, User
from app.modules.jobs.interface import Job
from app.modules.remnant_inventory.models import (
    Remnant,
    RemnantImportBatch,
    RemnantImportItem,
    RemnantMaterial,
    RemnantPart,
)


def _user(db, name: str, role: str | None = None) -> User:
    roles = [Role(code=role, name=role)] if role else []
    user = User(username=name, real_name=name, password_hash="x", roles=roles)
    db.add(user)
    db.flush()
    return user


def _ready_item(db, *, owner: User, suffix: str = "a") -> tuple[RemnantImportItem, RemnantMaterial]:
    source = StoredFile(
        bucket="dxf-original",
        storage_key=f"tests/confirm-{suffix}.dxf",
        original_name=f"confirm-{suffix}.dxf",
        file_ext=".dxf",
        size_bytes=100,
        sha256=suffix * 64,
        status="available",
        uploaded_by=owner.id,
    )
    material = RemnantMaterial(code=f"Q235{suffix.upper()}", family_code="Q235", enabled=True)
    batch = RemnantImportBatch(created_by=owner.id, total_count=1, pending_count=1)
    db.add_all([source, material, batch])
    db.flush()
    item = RemnantImportItem(
        batch_id=batch.id,
        source_file_id=source.id,
        dxf_file_id=source.id,
        source_sha256=source.sha256,
        source_ext=".dxf",
        status="pending_confirmation",
        material_candidates_json=[{"value": material.code, "evidence": []}],
        project_candidates_json=[{"value": "P-1", "evidence": []}],
        part_candidates_json=[{"value": "L-1", "evidence": []}],
    )
    db.add(item)
    db.flush()
    return item, material


def test_importer_can_correct_candidates_and_values_are_normalized(db) -> None:
    from app.modules.remnant_inventory.imports import update_import_item

    owner = _user(db, "confirm-owner")
    item, material = _ready_item(db, owner=owner)

    updated = update_import_item(
        db,
        item.id,
        actor=owner,
        thickness_mm="12.345",
        material_id=material.id,
        project_no="  PJ-002  ",
        parts=[" L-2 ", "L-2", "Ｌ-３"],
    )

    assert updated.corrected_thickness_mm == Decimal("12.345")
    assert updated.corrected_project_no == "PJ-002"
    assert updated.corrected_parts_json == ["L-2", "L-3"]


def test_non_owner_cannot_edit_but_admin_can(db) -> None:
    from app.modules.remnant_inventory.imports import update_import_item

    owner = _user(db, "confirm-owner-2")
    outsider = _user(db, "confirm-outsider")
    admin = _user(db, "confirm-admin", "admin")
    item, material = _ready_item(db, owner=owner, suffix="b")

    with pytest.raises(HTTPException) as captured:
        update_import_item(db, item.id, actor=outsider, thickness_mm="8")
    assert captured.value.status_code == 403

    assert update_import_item(
        db, item.id, actor=admin, material_id=material.id, thickness_mm="8"
    ).corrected_thickness_mm == Decimal("8.000")


@pytest.mark.parametrize("value", ["0", "-1", "1.2345"])
def test_thickness_must_be_positive_with_at_most_three_decimals(db, value: str) -> None:
    from app.modules.remnant_inventory.imports import update_import_item

    owner = _user(db, f"bad-thickness-{value}")
    item, _material = _ready_item(db, owner=owner, suffix=str(len(value)))
    with pytest.raises(HTTPException) as captured:
        update_import_item(db, item.id, actor=owner, thickness_mm=value)
    assert captured.value.detail["code"] == "REMNANT_THICKNESS_INVALID"


def test_bulk_thickness_updates_only_selected_pending_items(db) -> None:
    from app.modules.remnant_inventory.imports import bulk_apply_thickness

    owner = _user(db, "bulk-owner")
    first, _ = _ready_item(db, owner=owner, suffix="c")
    second, _ = _ready_item(db, owner=owner, suffix="d")

    changed = bulk_apply_thickness(
        db, first.batch_id, item_ids=[first.id], thickness_mm="6", actor=owner
    )

    db.refresh(first)
    db.refresh(second)
    assert changed == [first.id]
    assert first.corrected_thickness_mm == Decimal("6.000")
    assert second.corrected_thickness_mm is None


def test_confirmation_is_partial_and_repeated_identity_is_stable(db) -> None:
    from app.modules.remnant_inventory.imports import confirm_import_items, update_import_item

    owner = _user(db, "confirm-repeat")
    ready, material = _ready_item(db, owner=owner, suffix="e")
    invalid, _ = _ready_item(db, owner=owner, suffix="f")
    update_import_item(
        db,
        ready.id,
        actor=owner,
        thickness_mm="10",
        material_id=material.id,
        project_no="P-100",
        parts=["A-1", "A-2", "A-1"],
    )

    first = confirm_import_items(db, [ready.id, invalid.id], actor=owner)
    second = confirm_import_items(db, [ready.id], actor=owner)

    assert [entry.item_id for entry in first.confirmed] == [ready.id]
    assert first.invalid[0].item_id == invalid.id
    assert second.already_confirmed[0].remnant_id == first.confirmed[0].remnant_id
    assert db.query(Remnant).count() == 1
    assert [part.part_no for part in db.query(RemnantPart).all()] == ["A-1", "A-2"]


def test_retry_increments_attempt_and_cancel_marks_unconfirmed_files_deleted(db) -> None:
    from app.modules.remnant_inventory.imports import cancel_import_batch, retry_import_item

    owner = _user(db, "retry-owner")
    item, _material = _ready_item(db, owner=owner, suffix="1")
    item.status = "failed"
    item.error_code = "REMNANT_PARSE_FAILED"
    db.flush()

    dispatch = retry_import_item(db, item.id, actor=owner)
    assert item.attempt == 2
    assert dispatch.parse_attempts == {item.id: 2}

    cancelled = cancel_import_batch(db, item.batch_id, actor=owner, request_id="req-remnant-cancel")
    db.refresh(item)
    source = db.get(StoredFile, item.source_file_id)
    assert cancelled == [item.id]
    assert item.status == "cancelled"
    assert source.status == "deleted"
    assert db.get(Job, item.parse_job_id).status == "cancelled"


def test_cancel_after_partial_confirmation_is_terminal(db) -> None:
    from app.modules.remnant_inventory.execution import recalculate_batch_counters
    from app.modules.remnant_inventory.imports import cancel_import_batch

    owner = _user(db, "partial-cancel-owner")
    pending, _material = _ready_item(db, owner=owner, suffix="7")
    confirmed, _ = _ready_item(db, owner=owner, suffix="8")
    old_batch_id = confirmed.batch_id
    confirmed.batch_id = pending.batch_id
    confirmed.status = "confirmed"
    db.flush()
    db.query(RemnantImportBatch).filter(RemnantImportBatch.id == old_batch_id).delete()
    recalculate_batch_counters(db, pending.batch_id)

    cancelled = cancel_import_batch(
        db, pending.batch_id, actor=owner, request_id="req-partial-cancel"
    )

    batch = db.get(RemnantImportBatch, pending.batch_id)
    assert cancelled == [pending.id]
    assert batch.confirmed_count == 1
    assert batch.cancelled_count == 1
    assert batch.status == "cancelled"
