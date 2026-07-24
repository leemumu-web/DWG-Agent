from __future__ import annotations

import importlib
from dataclasses import replace
from decimal import Decimal

import pytest

from domain import ParentPartEvidence, SourcePart
from spec_parser import classify_normalized_spec
from weights import fabricated_parent_unit_weight


def _splitter():
    try:
        return importlib.import_module("splitter")
    except ModuleNotFoundError as exc:
        pytest.fail(f"canonical splitter module is missing: {exc}")


def _parent(spec: str, *, original_qty: str = "3") -> ParentPartEvidence:
    import re

    match = re.fullmatch(r"(BH|BOX|BT)([0-9.]+)\*([0-9.]+)\*([0-9.]+)\*([0-9.]+)", spec)
    assert match is not None
    profile = match.group(1)
    height, width, web, flange = (Decimal(value) for value in match.groups()[1:])
    length = Decimal("3704")
    theory = fabricated_parent_unit_weight(profile, height, width, web, flange, length)
    qty = Decimal(original_qty)
    source = SourcePart(
        source_sheet="原表",
        source_row=15,
        source_seq="source-9",
        batch="B1",
        component_no="C1",
        component_qty=Decimal("2"),
        part_no="p1",
        original_spec=spec,
        material="Q355B",
        length=length,
        original_qty=qty,
        source_unit_net=theory,
        source_total_net=theory * qty,
        source_unit_gross=theory,
        source_total_gross=theory * qty,
        source_unit_area=Decimal("1.2"),
        source_total_area=Decimal("3.6"),
        classification=profile,
    )
    return ParentPartEvidence(
        source=source,
        normalized_type=profile,
        normalized_spec=spec,
        normalized_width=None,
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight_unrounded=theory,
        theoretical_total_weight_unrounded=theory * qty * source.component_qty,
        material_utilization=Decimal("1"),
        weight_validation_status="ok",
        weight_validation_details=(),
    )


@pytest.mark.parametrize(
    (
        "spec",
        "expected_labels",
        "expected_dimensions",
        "expected_quantities",
        "expected_multipliers",
    ),
    [
        (
            "BH700*300*16*30",
            ("BH腹", "BH翼"),
            (("16", "640"), ("30", "300")),
            ("3", "6"),
            ("1", "2"),
        ),
        (
            "BOX700*700*36*36",
            ("BOX腹", "BOX翼"),
            (("36", "628"), ("36", "700")),
            ("6", "6"),
            ("2", "2"),
        ),
        (
            "BT500*300*16*25",
            ("BT腹", "BT翼"),
            (("16", "475"), ("25", "300")),
            ("3", "3"),
            ("1", "1"),
        ),
    ],
)
def test_canonical_split_geometry_quantities_and_parent_conservation(
    spec: str,
    expected_labels: tuple[str, str],
    expected_dimensions: tuple[tuple[str, str], tuple[str, str]],
    expected_quantities: tuple[str, str],
    expected_multipliers: tuple[str, str],
) -> None:
    splitter = _splitter()
    parent = _parent(spec)
    classification = classify_normalized_spec(spec, material="Q355B")

    result = splitter.split_parent(parent, classification)

    assert not result.issues
    assert tuple(child.part_type for child in result.children) == expected_labels
    assert tuple((str(child.spec), str(child.width)) for child in result.children) == expected_dimensions
    assert tuple(str(child.quantity) for child in result.children) == expected_quantities
    assert tuple(
        str(child.theoretical_contribution_unrounded / (
            child.spec * child.width * parent.source.length * Decimal("7.85") / Decimal("1000000")
        ))
        for child in result.children
    ) == expected_multipliers
    assert sum(child.theoretical_contribution_unrounded for child in result.children) == (
        parent.theoretical_unit_weight_unrounded
    )
    assert parent.source.original_spec == spec


def test_split_children_keep_parent_identity_but_have_distinct_counts_and_import_ids() -> None:
    splitter = _splitter()
    parent = _parent("BOX700*700*36*36", original_qty="3")

    result = splitter.split_parent(
        parent,
        classify_normalized_spec(parent.source.original_spec, material=parent.source.material),
    )

    web, flange = result.children
    assert web.import_component_no == flange.import_component_no == "C1"
    assert web.import_part_no == "p1-BOX腹"
    assert flange.import_part_no == "p1-BOX翼"
    assert web.parent.source.source_seq == flange.parent.source.source_seq == "source-9"
    assert web.parent.source.original_qty == flange.parent.source.original_qty == Decimal("3")
    assert web.quantity == Decimal("6")
    assert web.quantity * parent.source.component_qty == Decimal("12")


def test_split_children_retain_parent_reference_and_main_role() -> None:
    splitter = _splitter()
    parent = _parent("BH700*300*16*30")

    result = splitter.split_parent(
        parent,
        classify_normalized_spec(parent.source.original_spec, material=parent.source.material),
    )
    web, flange = result.children

    assert web.is_main is True
    assert web.parent is parent
    assert flange.is_main is False
    assert flange.parent is parent
    assert flange.theoretical_contribution_unrounded > 0


def test_split_rejects_parent_theory_that_does_not_equal_weighted_children() -> None:
    splitter = _splitter()
    parent = _parent("BH700*300*16*30")
    inconsistent = replace(
        parent,
        theoretical_unit_weight_unrounded=(
            parent.theoretical_unit_weight_unrounded - Decimal("1")
        ),
    )

    result = splitter.split_parent(
        inconsistent,
        classify_normalized_spec(
            inconsistent.source.original_spec,
            material=inconsistent.source.material,
        ),
    )

    assert result.children == ()
    assert len(result.issues) == 1
    assert result.issues[0].level.value == "严重"
    assert result.issues[0].category == "拆板重量守恒异常"
    assert result.issues[0].field == "理单重"
    assert result.issues[0].affects_part is True


def test_invalid_inset_geometry_is_severe_and_has_no_children() -> None:
    splitter = _splitter()
    source = _parent("BH700*300*16*30").source
    invalid = ParentPartEvidence(
        source=replace(source, original_spec="BH50*300*16*30"),
        normalized_type="BH",
        normalized_spec="BH50*300*16*30",
        normalized_width=None,
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight_unrounded=None,
        theoretical_total_weight_unrounded=None,
        material_utilization=None,
        weight_validation_status="ok",
        weight_validation_details=(),
    )

    result = splitter.split_parent(
        invalid,
        classify_normalized_spec(invalid.normalized_spec, material="Q355B"),
    )

    assert result.children == ()
    assert len(result.issues) == 1
    assert result.issues[0].level.value == "严重"
    assert result.issues[0].category == "拆板几何异常"
    assert result.issues[0].affects_part is True


@pytest.mark.parametrize("spec", ["I20a", "HA700*300*16*30"])
def test_i_and_ha_cannot_enter_canonical_split(spec: str) -> None:
    splitter = _splitter()
    classification = classify_normalized_spec(spec, material="Q355B")

    with pytest.raises(ValueError, match="not a canonical split candidate"):
        splitter.split_parent(_parent("BH700*300*16*30"), classification)
