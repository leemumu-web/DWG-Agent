from __future__ import annotations

from dataclasses import dataclass

import pytest
from steel_dxf_split.box.manufacturing_ir import PhysicalPlateRole
from tools.box_acceptance.constraints import evaluate_human_constraints
from tools.box_acceptance.contracts import (
    AcceptanceConstraint,
    ConstraintStatus,
    EvidenceFile,
    EvidenceLevel,
    SampleContract,
)


@dataclass(frozen=True)
class _Group:
    roles: tuple[PhysicalPlateRole, ...]
    quantity: int


def _contract(*keys: str) -> SampleContract:
    evidence = EvidenceFile("01_全部原文件/test.dxf", 1, "0" * 64)
    return SampleContract(
        sample_id="test",
        family="BOX",
        source_sheet="first",
        category="test-category",
        evidence_level=EvidenceLevel.HUMAN_CONSTRAINT,
        original=evidence,
        evidence_files=(evidence,),
        constraints=tuple(AcceptanceConstraint(key, key) for key in keys),
        human_wording="人工约束",
    )


def _separate_groups() -> tuple[_Group, ...]:
    return tuple(
        _Group((role,), 1)
        for role in (
            PhysicalPlateRole.WEB_LEFT,
            PhysicalPlateRole.WEB_RIGHT,
            PhysicalPlateRole.FLANGE_TOP,
            PhysicalPlateRole.FLANGE_BOTTOM,
        )
    )


def _merged_groups() -> tuple[_Group, ...]:
    return (
        _Group(
            (PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT),
            2,
        ),
        _Group(
            (PhysicalPlateRole.FLANGE_TOP, PhysicalPlateRole.FLANGE_BOTTOM),
            2,
        ),
    )


@pytest.mark.parametrize("key", ("web_quantity", "web_deduplication"))
def test_web_merge_constraints_use_actual_output_groups(key: str) -> None:
    passed = evaluate_human_constraints(_contract(key), _merged_groups())
    failed = evaluate_human_constraints(_contract(key), _separate_groups())

    assert passed.results[0].status is ConstraintStatus.PASS
    assert failed.results[0].status is ConstraintStatus.FAIL


def test_flange_deduplication_uses_actual_output_groups() -> None:
    passed = evaluate_human_constraints(
        _contract("flange_deduplication"),
        _merged_groups(),
    )
    failed = evaluate_human_constraints(
        _contract("flange_deduplication"),
        _separate_groups(),
    )

    assert passed.results[0].status is ConstraintStatus.PASS
    assert failed.results[0].status is ConstraintStatus.FAIL


@pytest.mark.parametrize(
    "key",
    ("flange_role_order", "flange_length", "web_hole_spacing"),
)
def test_constraints_without_a_unique_external_value_remain_unknown(key: str) -> None:
    evaluation = evaluate_human_constraints(_contract(key), _merged_groups())

    assert evaluation.results[0].status is ConstraintStatus.UNKNOWN
    assert "外部" in evaluation.results[0].reason


def test_formal_output_constraint_reflects_candidate_availability() -> None:
    passed = evaluate_human_constraints(
        _contract("formal_output_required"),
        _merged_groups(),
        output_available=True,
    )
    failed = evaluate_human_constraints(
        _contract("formal_output_required"),
        (),
        output_available=False,
    )

    assert passed.results[0].status is ConstraintStatus.PASS
    assert failed.results[0].status is ConstraintStatus.FAIL


def test_compound_contract_evaluates_every_constraint_in_declared_order() -> None:
    contract = _contract(
        "web_deduplication",
        "flange_deduplication",
        "flange_length",
    )

    evaluation = evaluate_human_constraints(contract, _merged_groups())

    assert [item.key for item in evaluation.results] == [
        "web_deduplication",
        "flange_deduplication",
        "flange_length",
    ]
    assert [item.status for item in evaluation.results] == [
        ConstraintStatus.PASS,
        ConstraintStatus.PASS,
        ConstraintStatus.UNKNOWN,
    ]


def test_unknown_constraint_key_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported human constraint"):
        evaluate_human_constraints(_contract("sample_specific_guess"), _merged_groups())
