"""Canonical BH/BOX/BT adapter built on the shared pure geometry helper."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from domain import ParentPartEvidence, SplitPart
from multi_split.profile import split_fabricated_geometry
from quality import IssueLevel, QualityIssue
from spec_parser import ClassificationResult, SplitPolicy
from weights import plate_unit_weight

_FABRICATED_SPEC = re.compile(
    r"^(BH|BOX|BT)([0-9]+(?:\.[0-9]+)?)\*([0-9]+(?:\.[0-9]+)?)"
    r"\*([0-9]+(?:\.[0-9]+)?)\*([0-9]+(?:\.[0-9]+)?)$"
)

_DISPLAY_FIELDS = (
    "source_unit_net",
    "source_total_net",
    "source_unit_gross",
    "source_total_gross",
    "source_unit_area",
    "source_total_area",
    "density_value",
    "density_source",
    "theoretical_unit_weight",
    "theoretical_total_weight",
    "material_utilization",
    "weight_validation_status",
    "weight_validation_details",
)


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
        expected_value="BH/BOX/BT 正尺寸且内嵌尺寸大于0",
        absolute_error=None,
        relative_error=None,
        affects_part=True,
        density_source=parent.density_source,
        description=description,
    )


def split_parent(
    parent: ParentPartEvidence,
    classification: ClassificationResult,
) -> CanonicalSplitResult:
    allowed = {SplitPolicy.BH, SplitPolicy.BOX, SplitPolicy.BT}
    if classification.split_policy not in allowed:
        raise ValueError(
            f"{classification.original_spec!r} is not a canonical split candidate"
        )
    match = _FABRICATED_SPEC.fullmatch(classification.normalized_spec)
    if match is None or match.group(1) != classification.split_policy.value:
        issue = _geometry_issue(parent, "已确认拆板类别与截面规格不一致")
        return CanonicalSplitResult((), (issue,))

    profile = match.group(1)
    height, width, web, flange = (
        Decimal(value) for value in match.groups()[1:]
    )
    try:
        geometry = split_fabricated_geometry(
            profile,
            height,
            width,
            web,
            flange,
        )
    except ValueError as exc:
        return CanonicalSplitResult((), (_geometry_issue(parent, str(exc)),))

    source = parent.source
    children = tuple(
        SplitPart(
            parent=parent,
            part_type=child.part_type,
            import_component_no=source.component_no,
            import_part_no=f"{source.part_no}-{child.part_type}",
            spec=child.thickness,
            width=child.width,
            quantity=source.original_qty * child.quantity_multiplier,
            is_main=child.is_main,
            theoretical_contribution_unrounded=(
                plate_unit_weight(child.thickness, child.width, source.length)
                * child.quantity_multiplier
            ),
        )
        for child in geometry
    )
    return CanonicalSplitResult(children, ())


def parent_display_values(child: SplitPart) -> dict[str, object]:
    """Return parent evidence only for the main/web output row."""
    if not child.is_main:
        return {field: None for field in _DISPLAY_FIELDS}
    parent = child.parent
    source = parent.source
    return {
        "source_unit_net": source.source_unit_net,
        "source_total_net": source.source_total_net,
        "source_unit_gross": source.source_unit_gross,
        "source_total_gross": source.source_total_gross,
        "source_unit_area": source.source_unit_area,
        "source_total_area": source.source_total_area,
        "density_value": parent.density_value,
        "density_source": parent.density_source,
        "theoretical_unit_weight": parent.theoretical_unit_weight_unrounded,
        "theoretical_total_weight": parent.theoretical_total_weight_unrounded,
        "material_utilization": parent.material_utilization,
        "weight_validation_status": parent.weight_validation_status,
        "weight_validation_details": parent.weight_validation_details,
    }
