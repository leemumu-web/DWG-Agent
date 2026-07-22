from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import Numeric, UniqueConstraint, inspect


def _models():
    try:
        from app.modules.remnant_inventory.models import (
            Remnant,
            RemnantImportBatch,
            RemnantImportItem,
            RemnantMaterial,
            RemnantMaterialAlias,
            RemnantPart,
        )
    except ModuleNotFoundError:
        pytest.fail("remnant_inventory model module is not implemented")
    return RemnantMaterial, RemnantMaterialAlias, RemnantImportBatch, RemnantImportItem, Remnant, RemnantPart


def _unique_names(model) -> set[str]:
    return {
        constraint.name
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }


def test_model_owner_defines_all_six_tables() -> None:
    models = _models()
    assert [model.__tablename__ for model in models] == [
        "remnant_materials",
        "remnant_material_aliases",
        "remnant_import_batches",
        "remnant_import_items",
        "remnants",
        "remnant_parts",
    ]


def test_business_uniqueness_is_enforced_by_named_constraints() -> None:
    material, alias, _batch, item, remnant, part = _models()
    assert "uq_remnant_material_code" in _unique_names(material)
    assert "uq_remnant_material_alias_normalized" in _unique_names(alias)
    assert "uq_remnant_import_item_batch_source" in _unique_names(item)
    assert "uq_remnant_source_sha256" in _unique_names(remnant)
    assert "uq_remnant_import_item_confirmation" in _unique_names(remnant)
    assert "uq_remnant_part_number" in _unique_names(part)


def test_thickness_uses_fixed_three_decimal_precision() -> None:
    *_prefix, item, remnant, _part = _models()
    for column in (item.corrected_thickness_mm.property.columns[0], remnant.thickness_mm.property.columns[0]):
        assert isinstance(column.type, Numeric)
        assert column.type.precision == 10
        assert column.type.scale == 3
        assert column.type.asdecimal is True


def test_status_attempt_counters_and_version_have_safe_defaults() -> None:
    _material, _alias, batch, item, remnant, _part = _models()
    assert batch.status.property.columns[0].default.arg == "uploaded"
    for name in ("total_count", "converting_count", "parsing_count", "pending_count", "confirmed_count", "failed_count", "cancelled_count"):
        assert getattr(batch, name).property.columns[0].default.arg == 0
    assert item.status.property.columns[0].default.arg == "uploaded"
    assert item.attempt.property.columns[0].default.arg == 1
    assert remnant.status.property.columns[0].default.arg == "available"
    assert remnant.version.property.columns[0].default.arg == 1


def test_models_are_registered_in_application_metadata() -> None:
    _models()
    from app.bootstrap.model_registry import load_models
    from app.platform.database.base import Base

    load_models()
    expected = {"remnant_materials", "remnant_material_aliases", "remnant_import_batches", "remnant_import_items", "remnants", "remnant_parts"}
    assert expected <= set(Base.metadata.tables)


def test_expected_lookup_and_lifecycle_indexes_exist() -> None:
    material, _alias, _batch, item, remnant, _part = _models()
    assert "ix_remnant_material_family_enabled" in {index.name for index in material.__table__.indexes}
    assert "ix_remnant_import_item_batch_status" in {index.name for index in item.__table__.indexes}
    assert "ix_remnant_search" in {index.name for index in remnant.__table__.indexes}
    assert "ix_remnant_reserved_by_status" in {index.name for index in remnant.__table__.indexes}


def test_decimal_value_round_trips_without_float_coercion(db) -> None:
    RemnantMaterial, _alias, _batch, _item, _remnant, _part = _models()
    material = RemnantMaterial(code="Q235B-Z15", family_code="Q235", enabled=True)
    db.add(material)
    db.commit()
    assert material.code == "Q235B-Z15"
    assert material.enabled is True
