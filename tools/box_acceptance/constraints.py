"""Conservative evaluation of first-drawing human production constraints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from steel_dxf_split.box.manufacturing_ir import PhysicalPlateRole

from .contracts import ConstraintResult, ConstraintStatus, SampleContract


class OutputGroup(Protocol):
    roles: tuple[PhysicalPlateRole, ...]
    quantity: int


@dataclass(frozen=True, slots=True)
class ConstraintEvaluation:
    results: tuple[ConstraintResult, ...]


_WEB_ROLES = (PhysicalPlateRole.WEB_LEFT, PhysicalPlateRole.WEB_RIGHT)
_FLANGE_ROLES = (
    PhysicalPlateRole.FLANGE_TOP,
    PhysicalPlateRole.FLANGE_BOTTOM,
)


def _merged_exactly_once(
    groups: tuple[OutputGroup, ...],
    expected_roles: tuple[PhysicalPlateRole, PhysicalPlateRole],
) -> bool:
    family = set(expected_roles)
    relevant = tuple(group for group in groups if set(group.roles) <= family)
    return (
        len(relevant) == 1
        and relevant[0].roles == expected_roles
        and relevant[0].quantity == 2
    )


def _merge_result(
    *,
    key: str,
    groups: tuple[OutputGroup, ...],
    roles: tuple[PhysicalPlateRole, PhysicalPlateRole],
    label: str,
) -> ConstraintResult:
    passed = _merged_exactly_once(groups, roles)
    return ConstraintResult(
        key=key,
        status=ConstraintStatus.PASS if passed else ConstraintStatus.FAIL,
        reason=(
            f"实际产物将两块相同{label}合并为一个数量 2 的输出组"
            if passed
            else f"实际产物没有将两块相同{label}合并为一个数量 2 的输出组"
        ),
    )


def _unknown(key: str, subject: str) -> ConstraintResult:
    return ConstraintResult(
        key=key,
        status=ConstraintStatus.UNKNOWN,
        reason=f"人工说明确认{subject}曾出错，但没有提供唯一外部正确值，不能由当前程序自证正确",
    )


def evaluate_human_constraints(
    contract: SampleContract,
    groups: tuple[OutputGroup, ...],
    *,
    output_available: bool = True,
) -> ConstraintEvaluation:
    """Evaluate only facts directly authorized by a human evidence contract."""

    results: list[ConstraintResult] = []
    for constraint in contract.constraints:
        key = constraint.key
        if key in {"web_quantity", "web_deduplication"}:
            results.append(
                _merge_result(
                    key=key,
                    groups=groups,
                    roles=_WEB_ROLES,
                    label="腹板",
                )
            )
        elif key == "flange_deduplication":
            results.append(
                _merge_result(
                    key=key,
                    groups=groups,
                    roles=_FLANGE_ROLES,
                    label="翼缘板",
                )
            )
        elif key == "flange_role_order":
            results.append(_unknown(key, "上下翼缘角色"))
        elif key == "flange_length":
            results.append(_unknown(key, "翼缘长度"))
        elif key == "web_hole_spacing":
            results.append(_unknown(key, "腹板孔距"))
        elif key == "formal_output_required":
            results.append(
                ConstraintResult(
                    key=key,
                    status=(
                        ConstraintStatus.PASS
                        if output_available
                        else ConstraintStatus.FAIL
                    ),
                    reason=(
                        "已形成正式候选产物"
                        if output_available
                        else "没有形成正式候选产物"
                    ),
                )
            )
        else:
            raise ValueError(f"unsupported human constraint: {key}")
    return ConstraintEvaluation(results=tuple(results))
