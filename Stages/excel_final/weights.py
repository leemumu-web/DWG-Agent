"""Unrounded parent-level weight formulas and physical source validation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum

from domain import ParentPartEvidence, SourcePart
from fabricated_profile import FabricatedProfile
from quality import IssueLevel, QualityIssue

STEEL_DENSITY = Decimal("7.85")
SOURCE_CHAIN_TOLERANCE = Decimal("0.1")
ABSOLUTE_THEORY_TOLERANCE = Decimal("0.01")
PASS_RELATIVE_TOLERANCE = Decimal("0.005")
WARNING_RELATIVE_TOLERANCE = Decimal("0.02")


class TheoryBasis(StrEnum):
    GEOMETRY = "geometry"
    HANDBOOK = "handbook"


class AssessmentLevel(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    SEVERE = "severe"


@dataclass(frozen=True, slots=True)
class DeviationAssessment:
    level: AssessmentLevel
    absolute_error: Decimal
    relative_error: Decimal


@dataclass(frozen=True, slots=True)
class WeightValidationResult:
    evidence: ParentPartEvidence
    issues: tuple[QualityIssue, ...]


def plate_unit_weight(
    thickness: Decimal,
    width: Decimal,
    length: Decimal,
    density: Decimal = STEEL_DENSITY,
) -> Decimal:
    return thickness * width * length * density / Decimal("1000000")


def profile_unit_weight(linear_weight: Decimal, length: Decimal) -> Decimal:
    return linear_weight * length / Decimal("1000")


def fabricated_parent_unit_weight(
    profile: str,
    height: Decimal,
    width: Decimal,
    web_thickness: Decimal,
    flange_thickness: Decimal,
    length: Decimal,
    density: Decimal = STEEL_DENSITY,
) -> Decimal:
    if length <= 0:
        raise ValueError(f"fabricated profile has non-positive geometry: {profile}")
    fabricated = FabricatedProfile(
        profile,
        height,
        width,
        web_thickness,
        flange_thickness,
    )
    return (
        fabricated.cross_section_area
        * length
        * density
        / Decimal("1000000")
    )


def rectangular_surface_area(
    thickness: Decimal,
    width: Decimal,
    length: Decimal,
) -> Decimal:
    if min(thickness, width, length) <= 0:
        raise ValueError("rectangular dimensions must be positive")
    return Decimal("2") * (
        thickness * width + thickness * length + width * length
    ) / Decimal("1000000")


def round_weight_for_output(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def round_area_for_output(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def assess_theory_against_gross(
    theoretical: Decimal,
    source_gross: Decimal,
) -> DeviationAssessment:
    absolute_error = abs(source_gross - theoretical)
    relative_error = (
        absolute_error / abs(theoretical)
        if theoretical != 0
        else Decimal("Infinity")
    )
    if (
        absolute_error <= ABSOLUTE_THEORY_TOLERANCE
        or relative_error <= PASS_RELATIVE_TOLERANCE
    ):
        level = AssessmentLevel.PASS
    elif relative_error <= WARNING_RELATIVE_TOLERANCE:
        level = AssessmentLevel.WARNING
    else:
        level = AssessmentLevel.SEVERE
    return DeviationAssessment(level, absolute_error, relative_error)


def assess_net_against_theory(
    source_net: Decimal,
    theoretical: Decimal,
) -> DeviationAssessment:
    excess = source_net - theoretical
    if excess <= 0:
        return DeviationAssessment(AssessmentLevel.PASS, abs(excess), Decimal("0"))
    relative_error = excess / abs(theoretical) if theoretical != 0 else Decimal("Infinity")
    if relative_error <= PASS_RELATIVE_TOLERANCE:
        level = AssessmentLevel.PASS
    elif relative_error <= WARNING_RELATIVE_TOLERANCE:
        level = AssessmentLevel.WARNING
    else:
        level = AssessmentLevel.SEVERE
    return DeviationAssessment(level, excess, relative_error)


def _issue_level(level: AssessmentLevel) -> IssueLevel:
    return IssueLevel.WARNING if level is AssessmentLevel.WARNING else IssueLevel.SEVERE


def _quality_issue(
    source: SourcePart,
    *,
    level: IssueLevel,
    category: str,
    field: str,
    actual: object,
    expected: object,
    absolute_error: Decimal | None,
    relative_error: Decimal | None,
    density_source: str,
    description: str,
) -> QualityIssue:
    return QualityIssue(
        level=level,
        category=category,
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        component_no=source.component_no,
        part_no=source.part_no,
        spec=source.original_spec,
        field=field,
        actual_value=actual,
        expected_value=expected,
        absolute_error=absolute_error,
        relative_error=relative_error,
        affects_part=level is IssueLevel.SEVERE,
        density_source=density_source,
        description=description,
    )


def validate_parent_weights(
    source: SourcePart,
    *,
    normalized_type: str,
    normalized_spec: str,
    normalized_width: Decimal | None,
    density_value: Decimal | None,
    density_source: str,
    theoretical_unit_weight: Decimal | None,
    theory_basis: TheoryBasis,
    report_missing_source_weights: bool = True,
) -> WeightValidationResult:
    issues: list[QualityIssue] = []
    source_fields = (
        ("单净重", source.source_unit_net),
        ("总净重", source.source_total_net),
        ("单毛重", source.source_unit_gross),
        ("总毛重", source.source_total_gross),
    )
    for field, value in source_fields:
        if value is None and report_missing_source_weights:
            issues.append(_quality_issue(
                source,
                level=IssueLevel.WARNING,
                category="源重量缺失",
                field=field,
                actual=None,
                expected="非空源值",
                absolute_error=None,
                relative_error=None,
                density_source=density_source,
                description=f"{field}缺失，未用理论值回填",
            ))

    chains = (
        ("总净重", source.source_unit_net, source.source_total_net),
        ("总毛重", source.source_unit_gross, source.source_total_gross),
    )
    for field, unit_value, total_value in chains:
        if unit_value is None or total_value is None:
            continue
        expected = unit_value * source.original_qty
        absolute_error = abs(total_value - expected)
        if absolute_error > SOURCE_CHAIN_TOLERANCE:
            issues.append(_quality_issue(
                source,
                level=IssueLevel.SEVERE,
                category="源重量链异常",
                field=field,
                actual=total_value,
                expected=expected,
                absolute_error=absolute_error,
                relative_error=absolute_error / abs(expected) if expected else None,
                density_source=density_source,
                description=f"{field}不等于对应单重乘原数量",
            ))

    net_gross_pairs = (
        ("单净重", source.source_unit_net, source.source_unit_gross),
        ("总净重", source.source_total_net, source.source_total_gross),
    )
    for field, net_value, gross_value in net_gross_pairs:
        if net_value is None or gross_value is None:
            continue
        excess = net_value - gross_value
        if excess > SOURCE_CHAIN_TOLERANCE:
            issues.append(_quality_issue(
                source,
                level=IssueLevel.SEVERE,
                category="净重大于毛重",
                field=field,
                actual=net_value,
                expected=f"<={gross_value}",
                absolute_error=excess,
                relative_error=excess / abs(gross_value) if gross_value else None,
                density_source=density_source,
                description=f"{field}显著大于对应毛重",
            ))

    if theoretical_unit_weight is not None:
        theory_category = (
            "几何理论重与毛重"
            if theory_basis is TheoryBasis.GEOMETRY
            else "手册理论重与毛重"
        )
        theory_source_total = theoretical_unit_weight * source.original_qty
        gross_comparisons = (
            ("单毛重", theoretical_unit_weight, source.source_unit_gross),
            ("总毛重", theory_source_total, source.source_total_gross),
        )
        for field, theoretical, gross in gross_comparisons:
            if gross is None:
                continue
            assessment = assess_theory_against_gross(theoretical, gross)
            if assessment.level is AssessmentLevel.PASS:
                continue
            issues.append(_quality_issue(
                source,
                level=_issue_level(assessment.level),
                category=theory_category,
                field=field,
                actual=gross,
                expected=theoretical,
                absolute_error=assessment.absolute_error,
                relative_error=assessment.relative_error,
                density_source=density_source,
                description=f"{field}与父理论重量偏差超限",
            ))

    utilization: Decimal | None = None
    if (
        source.source_unit_net is not None
        and theoretical_unit_weight is not None
        and theoretical_unit_weight > 0
    ):
        utilization = source.source_unit_net / theoretical_unit_weight
        assessment = assess_net_against_theory(
            source.source_unit_net,
            theoretical_unit_weight,
        )
        if assessment.level is not AssessmentLevel.PASS:
            issues.append(_quality_issue(
                source,
                level=_issue_level(assessment.level),
                category="净重大于理论重",
                field="单净重",
                actual=source.source_unit_net,
                expected=f"<={theoretical_unit_weight}",
                absolute_error=assessment.absolute_error,
                relative_error=assessment.relative_error,
                density_source=density_source,
                description="单净重超过父理论毛坯重",
            ))

    if any(issue.level is IssueLevel.SEVERE for issue in issues):
        status = "severe_warning"
    elif any(issue.level is IssueLevel.WARNING for issue in issues):
        status = "warning"
    else:
        status = "ok"
    theoretical_total = (
        theoretical_unit_weight * source.original_qty * source.component_qty
        if theoretical_unit_weight is not None
        else None
    )
    evidence = ParentPartEvidence(
        source=source,
        normalized_type=normalized_type,
        normalized_spec=normalized_spec,
        normalized_width=normalized_width,
        density_value=density_value,
        density_source=density_source,
        theoretical_unit_weight_unrounded=theoretical_unit_weight,
        theoretical_total_weight_unrounded=theoretical_total,
        material_utilization=utilization,
        weight_validation_status=status,
        weight_validation_details=tuple(issue.description for issue in issues),
    )
    return WeightValidationResult(evidence=evidence, issues=tuple(issues))
