from __future__ import annotations

import importlib
from dataclasses import replace
from decimal import Decimal

import pytest

from domain import ParentPartEvidence, SourcePart
from spec_parser import classify_normalized_spec
from weights import plate_unit_weight, rectangular_surface_area, round_area_for_output, round_weight_for_output


def _part_builder():
    try:
        return importlib.import_module("part_builder")
    except ModuleNotFoundError as exc:
        pytest.fail(f"part builder module is missing: {exc}")


def _plate_parent(*, normalized_type: str = "板材") -> ParentPartEvidence:
    thickness = Decimal("10")
    width = Decimal("135")
    length = Decimal("250")
    quantity = Decimal("2")
    theory = plate_unit_weight(thickness, width, length)
    area = rectangular_surface_area(thickness, width, length)
    unit_weight = round_weight_for_output(theory)
    total_weight = round_weight_for_output(theory * quantity)
    source = SourcePart(
        source_sheet="原表",
        source_row=8,
        source_seq=1,
        batch="B1",
        component_no="C1",
        component_qty=Decimal("1"),
        part_no="p1",
        original_spec="PL10*135" if normalized_type == "板材" else "PL6*30",
        material="Q355B",
        length=length,
        original_qty=quantity,
        source_unit_net=unit_weight,
        source_total_net=total_weight,
        source_unit_gross=unit_weight,
        source_total_gross=total_weight,
        source_unit_area=round_area_for_output(area),
        source_total_area=round_area_for_output(area * quantity),
        classification=normalized_type,
    )
    return ParentPartEvidence(
        source=source,
        normalized_type=normalized_type,
        normalized_spec="10" if normalized_type == "板材" else "6*30",
        normalized_width=width if normalized_type == "板材" else None,
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85" if normalized_type == "板材" else "flat_steel:flat_steel",
        theoretical_unit_weight_unrounded=theory,
        theoretical_total_weight_unrounded=theory * quantity,
        material_utilization=unit_weight / theory,
        weight_validation_status="ok",
        weight_validation_details=(),
    )


def test_ordinary_plate_is_rect_only_when_all_evidence_is_proven() -> None:
    builder = _part_builder()
    parent = _plate_parent()

    decision = builder.infer_plate_rect(
        parent,
        cut_length=parent.source.length,
        identity_consistent=True,
    )

    assert decision.proven is True
    assert decision.file_value == "RECT"
    assert decision.reasons == ()
    assert decision.exclude_from_part is False


@pytest.mark.parametrize(
    ("change", "reason_fragment"),
    [
        ({"source_unit_net": Decimal("0.1")}, "单净重=单毛重"),
        ({"source_total_net": Decimal("0.2")}, "总净重=总毛重"),
        ({"source_unit_gross": Decimal("9")}, "单毛重=三位理论重"),
        ({"source_total_gross": Decimal("9")}, "总毛重=三位理论总重"),
        ({"source_unit_area": Decimal("9")}, "单表面积=两位六面面积"),
        ({"source_total_area": Decimal("9")}, "总表面积=两位六面总面积"),
    ],
)
def test_each_plate_rect_weight_and_area_condition_is_independently_required(
    change: dict[str, Decimal],
    reason_fragment: str,
) -> None:
    builder = _part_builder()
    parent = _plate_parent()
    changed = replace(parent, source=replace(parent.source, **change))

    decision = builder.infer_plate_rect(
        changed,
        cut_length=changed.source.length,
        identity_consistent=True,
    )

    assert decision.proven is False
    assert decision.file_value is None
    assert any(reason_fragment in reason for reason in decision.reasons)


def test_plate_rect_requires_cut_length_and_identity_consistency() -> None:
    builder = _part_builder()
    parent = _plate_parent()

    bad_cut = builder.infer_plate_rect(
        parent,
        cut_length=parent.source.length - Decimal("1"),
        identity_consistent=True,
    )
    bad_identity = builder.infer_plate_rect(
        parent,
        cut_length=parent.source.length,
        identity_consistent=False,
    )

    assert any("下料长度" in reason for reason in bad_cut.reasons)
    assert any("身份" in reason for reason in bad_identity.reasons)
    assert bad_identity.exclude_from_part is True


def test_flat_steel_never_infers_rect() -> None:
    builder = _part_builder()
    parent = _plate_parent(normalized_type="扁钢")

    decision = builder.infer_plate_rect(
        parent,
        cut_length=parent.source.length,
        identity_consistent=True,
    )

    assert decision.proven is False
    assert decision.file_value is None
    assert "扁钢不推断RECT" in decision.reasons


def _split_parent() -> tuple[ParentPartEvidence, tuple[object, ...]]:
    from splitter import split_parent
    from weights import fabricated_parent_unit_weight

    spec = "BOX700*700*36*36"
    length = Decimal("3704")
    quantity = Decimal("2")
    theory = fabricated_parent_unit_weight(
        "BOX", Decimal("700"), Decimal("700"), Decimal("36"), Decimal("36"), length
    )
    unit = round_weight_for_output(theory)
    total = round_weight_for_output(theory * quantity)
    source = SourcePart(
        source_sheet="原表",
        source_row=15,
        source_seq=2,
        batch="B1",
        component_no="C1",
        component_qty=Decimal("1"),
        part_no="p2",
        original_spec=spec,
        material="Q355B",
        length=length,
        original_qty=quantity,
        source_unit_net=unit,
        source_total_net=total,
        source_unit_gross=unit,
        source_total_gross=total,
        source_unit_area=Decimal("1"),
        source_total_area=Decimal("2"),
        classification="BOX",
    )
    parent = ParentPartEvidence(
        source=source,
        normalized_type="BOX",
        normalized_spec=spec,
        normalized_width=None,
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight_unrounded=theory,
        theoretical_total_weight_unrounded=theory * quantity,
        material_utilization=unit / theory,
        weight_validation_status="ok",
        weight_validation_details=(),
    )
    children = split_parent(parent, classify_normalized_spec(spec, material="Q355B")).children
    return parent, children


def test_split_outline_can_be_proven_or_informationally_unproven() -> None:
    builder = _part_builder()
    parent, children = _split_parent()

    proven = builder.infer_split_rect(
        parent,
        children,
        cut_length=parent.source.length,
        identity_consistent=True,
        geometry_valid=True,
    )
    unproven_parent = replace(
        parent,
        source=replace(parent.source, source_unit_net=parent.source.source_unit_net - Decimal("1")),
    )
    unproven = builder.infer_split_rect(
        unproven_parent,
        children,
        cut_length=parent.source.length,
        identity_consistent=True,
        geometry_valid=True,
    )

    assert proven.proven is True
    assert proven.file_value == "RECT"
    assert unproven.proven is False
    assert unproven.exclude_from_part is False
    assert unproven.issues[0].level.value == "信息"
    assert unproven.issues[0].category == "RECT未证明"


def test_split_severe_identity_or_geometry_failure_is_excluded() -> None:
    builder = _part_builder()
    parent, children = _split_parent()

    decision = builder.infer_split_rect(
        parent,
        children,
        cut_length=parent.source.length,
        identity_consistent=False,
        geometry_valid=False,
    )

    assert decision.proven is False
    assert decision.exclude_from_part is True
    assert any(issue.level.value == "严重" for issue in decision.issues)
