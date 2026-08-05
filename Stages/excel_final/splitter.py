"""Canonical BH/BOX/BT adapter built on the shared pure geometry helper."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from domain import ParentPartEvidence, SplitPart
from fabricated_profile import FabricatedProfileError, parse_fabricated_profile
from quality import IssueLevel, QualityIssue
from spec_parser import ClassificationResult, SplitPolicy
from weights import plate_unit_weight

@dataclass(frozen=True, slots=True)
class CanonicalSplitResult:
    children: tuple[SplitPart, ...]
    issues: tuple[QualityIssue, ...]


def _geometry_issue(parent: ParentPartEvidence, description: str) -> QualityIssue:
    source = parent.source
    return QualityIssue(
        level=IssueLevel.SEVERE,
        category="拆板几何异常",
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        component_no=source.component_no,
        part_no=source.part_no,
        spec=source.original_spec,
        field="截面型材",
        actual_value=source.original_spec,
        expected_value="BBH/BH/BOX/BT 正尺寸且内嵌尺寸大于0",
        absolute_error=None,
        relative_error=None,
        affects_part=True,
        density_source=parent.density_source,
        description=description,
    )


def _weight_conservation_issue(
    parent: ParentPartEvidence,
    child_theory: Decimal,
) -> QualityIssue:
    source = parent.source
    expected = parent.theoretical_unit_weight_unrounded
    absolute_error = None if expected is None else abs(child_theory - expected)
    relative_error = (
        absolute_error / abs(expected)
        if absolute_error is not None and expected
        else None
    )
    return QualityIssue(
        level=IssueLevel.SEVERE,
        category="拆板重量守恒异常",
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        component_no=source.component_no,
        part_no=source.part_no,
        spec=source.original_spec,
        field="理单重",
        actual_value=child_theory,
        expected_value=expected,
        absolute_error=absolute_error,
        relative_error=relative_error,
        affects_part=True,
        density_source=parent.density_source,
        description="拆板单块理论重乘块数之和不等于父构件理论重",
    )


def split_parent(
    parent: ParentPartEvidence,
    classification: ClassificationResult,
) -> CanonicalSplitResult:
    allowed = {SplitPolicy.BBH, SplitPolicy.BH, SplitPolicy.BOX, SplitPolicy.BT}
    if classification.split_policy not in allowed:
        raise ValueError(
            f"{classification.original_spec!r} is not a canonical split candidate"
        )
    try:
        fabricated = parse_fabricated_profile(classification.normalized_spec)
    except FabricatedProfileError as exc:
        issue = _geometry_issue(parent, str(exc))
        return CanonicalSplitResult((), (issue,))
    if (
        fabricated is None
        or fabricated.kind != classification.split_policy.value
    ):
        issue = _geometry_issue(parent, "已确认拆板类别与截面规格不一致")
        return CanonicalSplitResult((), (issue,))

    try:
        geometry = fabricated.children()
    except FabricatedProfileError as exc:
        return CanonicalSplitResult((), (_geometry_issue(parent, str(exc)),))

    source = parent.source
    children_list: list[SplitPart] = []
    for child in geometry:
        unit_weight = plate_unit_weight(
            child.thickness,
            child.width,
            source.length,
        )
        children_list.append(
            SplitPart(
                parent=parent,
                part_type=child.part_type,
                import_component_no=source.component_no,
                import_part_no=f"{source.part_no}-{child.part_type}",
                spec=child.thickness,
                width=child.width,
                quantity=source.original_qty * child.quantity_multiplier,
                is_main=child.is_main,
                theoretical_unit_weight_unrounded=unit_weight,
                theoretical_contribution_unrounded=(
                    unit_weight * child.quantity_multiplier
                ),
            )
        )
    children = tuple(children_list)
    child_theory = sum(
        (child.theoretical_contribution_unrounded for child in children),
        start=Decimal("0"),
    )
    if (
        parent.theoretical_unit_weight_unrounded is not None
        and child_theory != parent.theoretical_unit_weight_unrounded
    ):
        return CanonicalSplitResult(
            (),
            (_weight_conservation_issue(parent, child_theory),),
        )
    return CanonicalSplitResult(children, ())
