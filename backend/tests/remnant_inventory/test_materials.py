from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.bootstrap.seed import init_db
from app.modules.identity.interface import Role, User


def test_material_token_normalization_preserves_full_grade_suffix() -> None:
    from app.modules.remnant_inventory.materials import normalize_material_token

    assert normalize_material_token("  q235b－z15　") == "Q235B-Z15"


def test_standard_code_and_alias_resolve_to_enabled_material(db) -> None:
    from app.modules.remnant_inventory.materials import (
        create_material,
        replace_aliases,
        resolve_material_candidate,
    )

    material = create_material(db, code="Q235B-Z15", family_code="Q235", actor_id=None)
    replace_aliases(db, material=material, aliases=["Q235B Z15", "Q235B-Z 15"], actor_id=None)
    db.flush()

    assert resolve_material_candidate(db, "q235b-z15").id == material.id
    assert resolve_material_candidate(db, " q235b z15 ").id == material.id
    assert material.code == "Q235B-Z15"


def test_disabled_material_is_not_resolved(db) -> None:
    from app.modules.remnant_inventory.materials import create_material, resolve_material_candidate

    material = create_material(db, code="Q235D", family_code="Q235", actor_id=None)
    material.enabled = False
    db.flush()

    assert resolve_material_candidate(db, "Q235D") is None


def test_resolve_or_create_uses_full_code_as_family(db) -> None:
    from app.modules.remnant_inventory.materials import resolve_or_create_material

    material, created = resolve_or_create_material(db, code=" q355b ", actor_id=None)

    assert created is True
    assert (material.code, material.family_code, material.enabled) == ("Q355B", "Q355B", True)


def test_resolve_or_create_returns_existing_material(db) -> None:
    from app.modules.remnant_inventory.materials import resolve_or_create_material

    first, _created = resolve_or_create_material(db, code="Q355B", actor_id=None)
    second, created = resolve_or_create_material(db, code="q355b", actor_id=None)

    assert (second.id, created) == (first.id, False)


def test_resolve_or_create_does_not_reenable_disabled_material(db) -> None:
    from app.modules.remnant_inventory.materials import (
        create_material,
        resolve_or_create_material,
    )

    material = create_material(db, code="Q355B", family_code="Q355B", actor_id=None)
    material.enabled = False
    db.flush()

    with pytest.raises(HTTPException) as captured:
        resolve_or_create_material(db, code="q355b", actor_id=None)

    assert captured.value.status_code == 409
    assert captured.value.detail["code"] == "REMNANT_MATERIAL_DISABLED"


def test_auto_resolve_creates_one_full_grade_and_returns_same_row(db) -> None:
    from app.modules.remnant_inventory.materials import resolve_or_create_auto_material

    first_actor = User(username="auto-first", real_name="Auto First", password_hash="x")
    second_actor = User(username="auto-second", real_name="Auto Second", password_hash="x")
    db.add_all([first_actor, second_actor])
    db.flush()
    first, created, reenabled = resolve_or_create_auto_material(
        db,
        code=" q355b-z15 ",
        actor_id=first_actor.id,
    )
    second, repeated_created, repeated_reenabled = resolve_or_create_auto_material(
        db,
        code="Q355B-Z15",
        actor_id=second_actor.id,
    )

    assert (first.code, first.family_code) == ("Q355B-Z15", "Q355B-Z15")
    assert (second.id, created, reenabled) == (first.id, True, False)
    assert (repeated_created, repeated_reenabled) == (False, False)


def test_auto_resolve_reenables_disabled_material_without_changing_family(db) -> None:
    from app.modules.remnant_inventory.materials import (
        create_material,
        resolve_or_create_auto_material,
    )

    actor = User(username="auto-enable", real_name="Auto Enable", password_hash="x")
    db.add(actor)
    db.flush()
    material = create_material(db, code="Q390B", family_code="Q390", actor_id=None)
    material.enabled = False
    db.flush()

    resolved, created, reenabled = resolve_or_create_auto_material(
        db,
        code="q390b",
        actor_id=actor.id,
    )

    assert (resolved.id, created, reenabled) == (material.id, False, True)
    assert (resolved.enabled, resolved.family_code, resolved.updated_by) == (
        True,
        "Q390",
        actor.id,
    )


def test_auto_resolve_uses_existing_disabled_alias_instead_of_creating_code(db) -> None:
    from app.modules.remnant_inventory.materials import (
        create_material,
        replace_aliases,
        resolve_or_create_auto_material,
    )
    from app.modules.remnant_inventory.models import RemnantMaterial

    actor = User(username="auto-alias", real_name="Auto Alias", password_hash="x")
    db.add(actor)
    db.flush()
    material = create_material(db, code="Q355B", family_code="Q355", actor_id=None)
    replace_aliases(
        db,
        material=material,
        aliases=["Q355-B"],
        actor_id=None,
    )
    material.enabled = False
    db.flush()

    resolved, created, reenabled = resolve_or_create_auto_material(
        db,
        code=" q355-b ",
        actor_id=actor.id,
    )

    assert (resolved.id, created, reenabled) == (material.id, False, True)
    assert (resolved.code, resolved.family_code, resolved.enabled) == (
        "Q355B",
        "Q355",
        True,
    )
    assert db.query(RemnantMaterial).count() == 1


def test_family_search_expands_only_enabled_family_members(db) -> None:
    from app.modules.remnant_inventory.materials import create_material, material_ids_for_search

    q235b = create_material(db, code="Q235B", family_code="Q235", actor_id=None)
    q235d = create_material(db, code="Q235D", family_code="Q235", actor_id=None)
    z15 = create_material(db, code="Q235B-Z15", family_code="Q235", actor_id=None)
    disabled = create_material(db, code="Q235C", family_code="Q235", actor_id=None)
    create_material(db, code="Q355B", family_code="Q355", actor_id=None)
    disabled.enabled = False
    db.flush()

    assert material_ids_for_search(db, z15.id, include_family=False) == [z15.id]
    assert material_ids_for_search(db, z15.id, include_family=True) == [q235b.id, z15.id, q235d.id]


def test_duplicate_normalized_alias_has_stable_conflict(db) -> None:
    from app.modules.remnant_inventory.materials import create_material, replace_aliases

    first = create_material(db, code="Q235B", family_code="Q235", actor_id=None)
    second = create_material(db, code="Q355B", family_code="Q355", actor_id=None)
    replace_aliases(db, material=first, aliases=["普板"], actor_id=None)
    db.flush()

    with pytest.raises(HTTPException) as captured:
        replace_aliases(db, material=second, aliases=[" 普板 "], actor_id=None)
    assert captured.value.status_code == 409
    assert captured.value.detail["code"] == "REMNANT_MATERIAL_ALIAS_EXISTS"


def test_material_create_schema_rejects_blank_codes() -> None:
    from pydantic import ValidationError

    from app.modules.remnant_inventory.schemas import MaterialCreate

    with pytest.raises(ValidationError):
        MaterialCreate(code="  ", family_code="Q235")


def test_worker_can_use_but_only_admin_can_manage_catalog() -> None:
    from app.modules.remnant_inventory.access import can_manage_materials, can_use_remnants

    worker = User(
        username="w",
        real_name="W",
        password_hash="x",
        roles=[Role(code="operator", name="余料工人")],
    )
    admin = User(
        username="a", real_name="A", password_hash="x", roles=[Role(code="admin", name="管理员")]
    )
    viewer = User(
        username="v", real_name="V", password_hash="x", roles=[Role(code="viewer", name="只读")]
    )

    assert can_use_remnants(worker) is True
    assert can_manage_materials(worker) is False
    assert can_use_remnants(admin) is True
    assert can_manage_materials(admin) is True
    assert can_use_remnants(viewer) is False


def test_seed_installs_operator_role_and_three_tier_permissions(db) -> None:
    from app.modules.identity.interface import Permission

    init_db()
    roles = set(db.scalars(select(Role.code)).all())
    permissions = set(db.scalars(select(Permission.code)).all())
    assert "operator" in roles
    assert permissions == {"admin", "operator", "viewer"}
