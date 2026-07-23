"""One canonical processing engine shared by every Excel Final input adapter."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Protocol

from domain import ComponentSourceRow, ParentPartEvidence, PipelineOutcome, SourcePart
from fabricated_profile import FabricatedProfileError, parse_fabricated_profile
from handbook import HandbookLookupResult, LookupStatus
from part_builder import (
    PartCandidate,
    build_part_rows,
    candidate_from_parent,
    candidate_from_split,
)
from quality import IssueLevel, QualityIssue
from spec_parser import (
    ClassificationResult,
    LookupPolicy,
    SplitPolicy,
    classify_normalized_spec,
)
from splitter import parent_display_values, split_parent
from weights import (
    STEEL_DENSITY,
    TheoryBasis,
    fabricated_parent_unit_weight,
    plate_unit_weight,
    profile_unit_weight,
    round_weight_for_output,
    validate_parent_weights,
)
from writer_parts import write_canonical_workbook


class HandbookReader(Protocol):
    def lookup(
        self,
        category: object,
        normalized_spec: str,
        *,
        material: str | None = None,
    ) -> HandbookLookupResult: ...


@dataclass(frozen=True, slots=True)
class _ResolvedParent:
    source: SourcePart
    classification: ClassificationResult
    evidence: ParentPartEvidence
    lookup_missing: bool
    issues: tuple[QualityIssue, ...]


def _lookup_issue(
    source: SourcePart,
    classification: ClassificationResult,
    density_source: str,
) -> QualityIssue:
    reason = classification.reason or "指定类别的五金手册没有该规格"
    return QualityIssue(
        level=IssueLevel.WARNING,
        category="五金手册查无",
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        component_no=source.component_no,
        part_no=source.part_no,
        spec=source.original_spec,
        field="比重",
        actual_value="查无",
        expected_value="指定类别手册命中",
        absolute_error=None,
        relative_error=None,
        affects_part=False,
        density_source=density_source,
        description=f"{source.original_spec}: {reason}",
    )


def _fabricated_theory(
    classification: ClassificationResult,
    length: Decimal,
) -> Decimal | None:
    try:
        fabricated = parse_fabricated_profile(classification.normalized_spec)
    except FabricatedProfileError:
        return None
    if fabricated is None:
        return None
    try:
        return fabricated_parent_unit_weight(
            fabricated.kind,
            fabricated.height,
            fabricated.width,
            fabricated.web_thickness,
            fabricated.flange_thickness,
            length,
        )
    except ValueError:
        return None


def _fallback_bare_plate(
    classification: ClassificationResult,
) -> ClassificationResult:
    thickness, width = classification.normalized_spec.split("*", maxsplit=1)
    return replace(
        classification,
        normalized_type="板材",
        normalized_spec=thickness,
        normalized_width=Decimal(width),
        handbook_category=None,
        lookup_policy=LookupPolicy.PLATE_CONSTANT,
        reason="扁钢表查无，按裸厚宽板材处理",
    )


def _resolve_parent(source: SourcePart, handbook: HandbookReader) -> _ResolvedParent:
    classification = classify_normalized_spec(
        source.original_spec,
        material=source.material,
    )
    density_value: Decimal | None = None
    density_source = ""
    theoretical: Decimal | None = None
    lookup_missing = False
    issues: list[QualityIssue] = []
    theory_basis = TheoryBasis.HANDBOOK

    if classification.lookup_policy is LookupPolicy.FLAT_THEN_PLATE:
        lookup = handbook.lookup(
            classification.handbook_category,
            classification.normalized_spec,
            material=source.material,
        )
        if lookup.status is LookupStatus.HIT:
            classification = replace(
                classification,
                normalized_type="扁钢",
                lookup_policy=LookupPolicy.HANDBOOK,
            )
            density_value = lookup.value_kg_per_m
            density_source = lookup.source
            theoretical = profile_unit_weight(density_value, source.length)
        else:
            classification = _fallback_bare_plate(classification)

    if classification.lookup_policy is LookupPolicy.PLATE_CONSTANT:
        density_value = STEEL_DENSITY
        density_source = "plate_constant:7.85"
        theory_basis = TheoryBasis.GEOMETRY
        if classification.split_policy is not SplitPolicy.NONE:
            theoretical = _fabricated_theory(classification, source.length)
        elif classification.normalized_width is not None:
            theoretical = plate_unit_weight(
                Decimal(classification.normalized_spec),
                classification.normalized_width,
                source.length,
            )
    elif classification.lookup_policy is LookupPolicy.HANDBOOK and theoretical is None:
        lookup = handbook.lookup(
            classification.handbook_category,
            classification.normalized_spec,
            material=source.material,
        )
        density_value = lookup.value_kg_per_m
        density_source = lookup.source
        if lookup.status is LookupStatus.HIT and density_value is not None:
            theoretical = profile_unit_weight(density_value, source.length)
        else:
            lookup_missing = True
            issues.append(_lookup_issue(source, classification, density_source))
    elif classification.lookup_policy is LookupPolicy.SKIP:
        density_source = "explicit_skip"
    elif classification.lookup_policy is LookupPolicy.NOT_FOUND:
        density_source = "classification:not_found"
        lookup_missing = True
        issues.append(_lookup_issue(source, classification, density_source))

    classified_source = replace(source, classification=classification.normalized_type)
    validation = validate_parent_weights(
        classified_source,
        normalized_type=classification.normalized_type,
        normalized_spec=classification.normalized_spec,
        normalized_width=classification.normalized_width,
        density_value=density_value,
        density_source=density_source,
        theoretical_unit_weight=theoretical,
        theory_basis=theory_basis,
        report_missing_source_weights=classification.lookup_policy is not LookupPolicy.SKIP,
    )
    issues.extend(validation.issues)
    return _ResolvedParent(
        source=classified_source,
        classification=classification,
        evidence=validation.evidence,
        lookup_missing=lookup_missing,
        issues=tuple(issues),
    )


def _number_or_text(value: str) -> Decimal | str:
    try:
        return Decimal(value)
    except InvalidOperation:
        return value


def _rounded(value: Decimal | None) -> Decimal | None:
    return None if value is None else round_weight_for_output(value)


def _table_weight(value: Decimal | None, component_qty: Decimal) -> Decimal | None:
    return _rounded(None if value is None else value * component_qty)


def _status_label(status: str) -> str:
    return {"ok": "通过", "warning": "警告", "severe_warning": "严重"}[status]


def _source_issue(
    source: SourcePart,
    *,
    category: str,
    field: str,
    actual: object,
    expected: object,
    description: str,
) -> QualityIssue:
    return QualityIssue(
        level=IssueLevel.SEVERE,
        category=category,
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        component_no=source.component_no,
        part_no=source.part_no or None,
        spec=source.original_spec or None,
        field=field,
        actual_value=actual,
        expected_value=expected,
        absolute_error=None,
        relative_error=None,
        affects_part=True,
        density_source=None,
        description=description,
    )


def _validate_source_row(source: SourcePart) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []
    for field in source.invalid_fields:
        issues.append(_source_issue(
            source,
            category="关键字段缺失",
            field=field,
            actual=None,
            expected="非空源值",
            description=f"{field}缺失；保留审计行但不进入 part",
        ))

    required_values = {
        "构件编号": source.component_no,
        "零件号": source.part_no,
        "规格": source.original_spec,
        "材质": source.material,
    }
    recorded = set(source.invalid_fields)
    for field, value in required_values.items():
        if not value and field not in recorded:
            issues.append(_source_issue(
                source,
                category="关键字段缺失",
                field=field,
                actual=None,
                expected="非空源值",
                description=f"{field}缺失；保留审计行但不进入 part",
            ))

    positive_fields = (
        ("长度", source.length),
        ("数量", source.original_qty),
        ("构件数", source.component_qty),
    )
    for field, value in positive_fields:
        if field in recorded:
            continue
        if not value.is_finite():
            issues.append(_source_issue(
                source,
                category="物理量非法",
                field=field,
                actual=value,
                expected="有限数",
                description=f"{field}必须为有限数；保留审计行但不进入 part",
            ))
            continue
        if value <= 0:
            issues.append(_source_issue(
                source,
                category="物理量非法",
                field=field,
                actual=value,
                expected=">0",
                description=f"{field}必须为正数；保留审计行但不进入 part",
            ))

    nonnegative_fields = (
        ("单净重", source.source_unit_net),
        ("总净重", source.source_total_net),
        ("单毛重", source.source_unit_gross),
        ("总毛重", source.source_total_gross),
        ("单表面积", source.source_unit_area),
        ("总表面积", source.source_total_area),
    )
    for field, value in nonnegative_fields:
        if value is not None and not value.is_finite():
            issues.append(_source_issue(
                source,
                category="物理量非法",
                field=field,
                actual=value,
                expected="有限数",
                description=f"{field}必须为有限数；保留审计行但不进入 part",
            ))
        elif value is not None and value < 0:
            issues.append(_source_issue(
                source,
                category="物理量非法",
                field=field,
                actual=value,
                expected=">=0",
                description=f"{field}不能为负数；保留审计行但不进入 part",
            ))
    return tuple(issues)


def _invalid_organized_row(source: SourcePart) -> dict[str, object]:
    missing = set(source.invalid_fields)
    length = None if "长度" in missing else source.length
    quantity = None if "数量" in missing else source.original_qty
    return {
        "序号": source.source_seq,
        "构件编号": None if "构件编号" in missing else source.component_no,
        "导入构件编号": None if "构件编号" in missing else source.component_no,
        "构件数": None if "构件数" in missing else source.component_qty,
        "类型": "未分类",
        "班组": "",
        "批次": source.batch,
        "零件号": None if "零件号" in missing else source.part_no,
        "导入零件号": None if "零件号" in missing else source.part_no,
        "截面型材": None if "规格" in missing else source.original_spec,
        "规格": None,
        "宽度": None,
        "长度(mm)": length,
        "左进(mm)": None,
        "右进(mm)": None,
        "下料长度(mm)": length,
        "材质": None if "材质" in missing else source.material,
        "原数量": quantity,
        "数量": quantity,
        "总数": None,
        "总长(mm)": None,
        "比重": None,
        "比重来源": None,
        "理单重(kg)": None,
        "理总重(kg)": None,
        "单净重(kg)": source.source_unit_net,
        "总净重(kg)": source.source_total_net,
        "表净重(kg)": None,
        "单毛重(kg)": source.source_unit_gross,
        "总毛重(kg)": source.source_total_gross,
        "表毛重(kg)": None,
        "净材利用率": None,
        "重量核验": "严重",
        "单表面积(㎡)": source.source_unit_area,
        "总表面积(㎡)": source.source_total_area,
        "_source_sheet": source.source_sheet,
        "_source_row": source.source_row,
    }


def _organized_row(
    resolved: _ResolvedParent,
    *,
    part_type: str,
    import_part_no: str,
    spec: Decimal | str,
    width: Decimal | None,
    quantity: Decimal,
    display_parent: bool,
) -> dict[str, object]:
    source = resolved.source
    evidence = resolved.evidence
    total_count = quantity * source.component_qty
    density: object = None
    if display_parent:
        density = "查无" if resolved.lookup_missing else evidence.density_value
    theory_unit = evidence.theoretical_unit_weight_unrounded if display_parent else None
    theory_total = evidence.theoretical_total_weight_unrounded if display_parent else None
    unit_net = source.source_unit_net if display_parent else None
    total_net = source.source_total_net if display_parent else None
    unit_gross = source.source_unit_gross if display_parent else None
    total_gross = source.source_total_gross if display_parent else None
    return {
        "序号": source.source_seq,
        "构件编号": source.component_no,
        "导入构件编号": source.component_no,
        "构件数": source.component_qty,
        "类型": part_type,
        "班组": "",
        "批次": source.batch,
        "零件号": source.part_no,
        "导入零件号": import_part_no,
        "截面型材": source.original_spec,
        "规格": spec,
        "宽度": width,
        "长度(mm)": source.length,
        "左进(mm)": None,
        "右进(mm)": None,
        "下料长度(mm)": source.length,
        "材质": source.material,
        "原数量": source.original_qty,
        "数量": quantity,
        "总数": total_count,
        "总长(mm)": source.length * total_count,
        "比重": density,
        "比重来源": evidence.density_source if display_parent else None,
        "理单重(kg)": _rounded(theory_unit),
        "理总重(kg)": _rounded(theory_total),
        "单净重(kg)": unit_net,
        "总净重(kg)": total_net,
        "表净重(kg)": _table_weight(total_net, source.component_qty),
        "单毛重(kg)": unit_gross,
        "总毛重(kg)": total_gross,
        "表毛重(kg)": _table_weight(total_gross, source.component_qty),
        "净材利用率": evidence.material_utilization if display_parent else None,
        "重量核验": _status_label(evidence.weight_validation_status) if display_parent else None,
        "单表面积(㎡)": source.source_unit_area if display_parent else None,
        "总表面积(㎡)": source.source_total_area if display_parent else None,
        "_source_sheet": source.source_sheet,
        "_source_row": source.source_row,
    }


def _blocked_components(issues: Iterable[QualityIssue]) -> set[str]:
    return {
        issue.component_no
        for issue in issues
        if issue.level is IssueLevel.SEVERE
        and issue.category in {"构件编号冲突", "构件物理量非法"}
        and issue.component_no is not None
    }


def _apply_final_issue_status(
    rows: Iterable[dict[str, object]],
    issues: Iterable[QualityIssue],
) -> None:
    severe_sources = {
        (issue.source_sheet, issue.source_row)
        for issue in issues
        if issue.level is IssueLevel.SEVERE
    }
    warning_sources = {
        (issue.source_sheet, issue.source_row)
        for issue in issues
        if issue.level is IssueLevel.WARNING
    }
    severe_components = {
        issue.component_no
        for issue in issues
        if issue.level is IssueLevel.SEVERE
        and issue.category in {"构件编号冲突", "构件物理量非法"}
        and issue.component_no is not None
    }
    for row in rows:
        source_key = (row.get("_source_sheet"), row.get("_source_row"))
        if source_key in severe_sources or row.get("构件编号") in severe_components:
            row["重量核验"] = "严重"
        elif source_key in warning_sources and row.get("重量核验") == "通过":
            row["重量核验"] = "警告"


def process_canonical_records(
    source_path: str | Path,
    output_path: str | Path,
    *,
    parts: Iterable[SourcePart],
    component_rows: Iterable[ComponentSourceRow],
    reader_issues: Iterable[QualityIssue],
    handbook: HandbookReader,
    internal_output_path: str | Path | None = None,
) -> PipelineOutcome:
    """Process canonical records and atomically emit the six-sheet workbook."""
    initial_issues = list(reader_issues)
    issues = list(initial_issues)
    blocked = _blocked_components(initial_issues)
    cleaned_parts: list[SourcePart] = []
    organized_rows: list[dict[str, object]] = []
    candidates: list[PartCandidate] = []

    for source in parts:
        source_issues = _validate_source_row(source)
        if source_issues:
            issues.extend(source_issues)
            invalid_source = replace(source, classification="无效")
            cleaned_parts.append(invalid_source)
            organized_rows.append(_invalid_organized_row(invalid_source))
            continue
        resolved = _resolve_parent(source, handbook)
        cleaned_parts.append(resolved.source)
        issues.extend(resolved.issues)
        identity_consistent = source.component_no not in blocked
        classification = resolved.classification
        evidence = resolved.evidence

        if classification.split_policy is not SplitPolicy.NONE:
            split = split_parent(evidence, classification)
            issues.extend(split.issues)
            if not split.children:
                organized_rows.append(_organized_row(
                    resolved,
                    part_type=classification.normalized_type,
                    import_part_no=source.part_no,
                    spec=_number_or_text(classification.normalized_spec),
                    width=classification.normalized_width,
                    quantity=source.original_qty,
                    display_parent=True,
                ))
                continue
            for child in split.children:
                display = parent_display_values(child)
                organized_rows.append(_organized_row(
                    resolved,
                    part_type=child.part_type,
                    import_part_no=child.import_part_no,
                    spec=child.spec,
                    width=child.width,
                    quantity=child.quantity,
                    display_parent=display["density_source"] is not None,
                ))
                candidates.append(candidate_from_split(
                    child,
                    cut_length=source.length,
                    identity_consistent=identity_consistent,
                ))
            continue

        organized_rows.append(_organized_row(
            resolved,
            part_type=classification.normalized_type,
            import_part_no=source.part_no,
            spec=_number_or_text(classification.normalized_spec),
            width=classification.normalized_width,
            quantity=source.original_qty,
            display_parent=True,
        ))
        if classification.normalized_type in {"板材", "扁钢"}:
            candidates.append(candidate_from_parent(
                evidence,
                cut_length=source.length,
                identity_consistent=identity_consistent,
            ))

    part_result = build_part_rows(candidates)
    issues.extend(part_result.issues)
    _apply_final_issue_status(organized_rows, issues)
    return write_canonical_workbook(
        source_path,
        output_path,
        cleaned_parts=cleaned_parts,
        component_rows=component_rows,
        organized_rows=organized_rows,
        part_rows=part_result.rows,
        issues=issues,
        internal_output_path=internal_output_path,
    )
