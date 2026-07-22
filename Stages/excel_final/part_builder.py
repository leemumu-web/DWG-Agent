"""Strict RECT inference and per-component part-list projection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Iterable

from domain import ParentPartEvidence, SplitPart
from quality import IssueLevel, QualityIssue
from weights import rectangular_surface_area, round_area_for_output, round_weight_for_output

TYPE_PRIORITY = {
    "BH腹": 0,
    "BH翼": 1,
    "BOX腹": 2,
    "BOX翼": 3,
    "BT腹": 4,
    "BT翼": 5,
    "扁钢": 6,
    "板材": 7,
}


@dataclass(frozen=True, slots=True)
class RectDecision:
    proven: bool
    file_value: str | None
    reasons: tuple[str, ...]
    exclude_from_part: bool
    issues: tuple[QualityIssue, ...]


@dataclass(frozen=True, slots=True)
class PartCandidate:
    source_sheet: str
    source_row: int
    source_seq: str | int | None
    import_component_no: str
    import_part_no: str
    spec: Decimal | str
    width: Decimal | None
    cut_length: Decimal
    material: str
    child_quantity: Decimal
    component_quantity: Decimal
    part_type: str
    team: str
    graphic: str
    file_value: str | None
    excluded: bool


@dataclass(frozen=True, slots=True)
class PartRow:
    import_component_no: str
    import_part_no: str
    spec: Decimal | str
    width: Decimal | None
    cut_length: Decimal
    material: str
    summary: Decimal
    team: str
    graphic: str
    part_type: str
    file_value: str | None


@dataclass(frozen=True, slots=True)
class PartBuildResult:
    rows: tuple[PartRow, ...]
    issues: tuple[QualityIssue, ...]


def _rect_issue(
    parent: ParentPartEvidence,
    *,
    level: IssueLevel,
    category: str,
    reasons: tuple[str, ...],
) -> QualityIssue:
    source = parent.source
    return QualityIssue(
        level=level,
        category=category,
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        component_no=source.component_no,
        part_no=source.part_no,
        spec=source.original_spec,
        field="文件",
        actual_value="; ".join(reasons),
        expected_value="RECT 证据完整",
        absolute_error=None,
        relative_error=None,
        affects_part=level is IssueLevel.SEVERE,
        density_source=parent.density_source,
        description="; ".join(reasons),
    )


def _decision(
    parent: ParentPartEvidence,
    reasons: list[str],
    *,
    severe: bool,
) -> RectDecision:
    if not reasons:
        return RectDecision(True, "RECT", (), False, ())
    reasons_tuple = tuple(reasons)
    level = IssueLevel.SEVERE if severe else IssueLevel.INFO
    category = "RECT证据冲突" if severe else "RECT未证明"
    issue = _rect_issue(parent, level=level, category=category, reasons=reasons_tuple)
    return RectDecision(False, None, reasons_tuple, severe, (issue,))


def infer_plate_rect(
    parent: ParentPartEvidence,
    *,
    cut_length: Decimal,
    identity_consistent: bool,
) -> RectDecision:
    source = parent.source
    if parent.normalized_type == "扁钢":
        return _decision(parent, ["扁钢不推断RECT"], severe=False)
    reasons: list[str] = []
    if parent.normalized_type != "板材" or parent.normalized_width is None:
        reasons.append("仅普通板材可推断RECT")
    if source.source_unit_net != source.source_unit_gross:
        reasons.append("未证明单净重=单毛重")
    if source.source_total_net != source.source_total_gross:
        reasons.append("未证明总净重=总毛重")

    theory = parent.theoretical_unit_weight_unrounded
    if theory is None or source.source_unit_gross != round_weight_for_output(theory):
        reasons.append("未证明单毛重=三位理论重")
    expected_total = theory * source.original_qty if theory is not None else None
    if expected_total is None or source.source_total_gross != round_weight_for_output(expected_total):
        reasons.append("未证明总毛重=三位理论总重")

    if parent.normalized_width is not None:
        try:
            thickness = Decimal(parent.normalized_spec)
            area = rectangular_surface_area(thickness, parent.normalized_width, source.length)
        except (ValueError, ArithmeticError):
            area = None
    else:
        area = None
    if area is None or source.source_unit_area != round_area_for_output(area):
        reasons.append("未证明单表面积=两位六面面积")
    expected_area_total = area * source.original_qty if area is not None else None
    if (
        expected_area_total is None
        or source.source_total_area != round_area_for_output(expected_area_total)
    ):
        reasons.append("未证明总表面积=两位六面总面积")
    if cut_length != source.length:
        reasons.append("下料长度与理论计算长度不一致")
    if not identity_consistent:
        reasons.append("构件或零件身份不一致")
    severe = not identity_consistent or parent.weight_validation_status == "severe_warning"
    return _decision(parent, reasons, severe=severe)


def infer_split_rect(
    parent: ParentPartEvidence,
    children: tuple[object, ...],
    *,
    cut_length: Decimal,
    identity_consistent: bool,
    geometry_valid: bool,
) -> RectDecision:
    source = parent.source
    theory = parent.theoretical_unit_weight_unrounded
    reasons: list[str] = []
    if source.source_unit_net != source.source_unit_gross:
        reasons.append("未证明父单净重=单毛重")
    if source.source_total_net != source.source_total_gross:
        reasons.append("未证明父总净重=总毛重")
    if theory is None or source.source_unit_gross != round_weight_for_output(theory):
        reasons.append("未证明父单毛重=组合理论重")
    expected_total = theory * source.original_qty if theory is not None else None
    if expected_total is None or source.source_total_gross != round_weight_for_output(expected_total):
        reasons.append("未证明父总毛重=组合理论总重")
    if not geometry_valid or not children:
        reasons.append("拆板几何无效")
    if cut_length != source.length:
        reasons.append("下料长度存在进刀修正")
    if not identity_consistent:
        reasons.append("构件或零件身份不一致")
    severe = (
        not geometry_valid
        or not identity_consistent
        or parent.weight_validation_status == "severe_warning"
    )
    return _decision(parent, reasons, severe=severe)


def candidate_from_parent(
    parent: ParentPartEvidence,
    rect: RectDecision,
    *,
    cut_length: Decimal,
    team: str = "",
) -> PartCandidate:
    source = parent.source
    spec: Decimal | str
    try:
        spec = Decimal(parent.normalized_spec)
    except ArithmeticError:
        spec = parent.normalized_spec
    return PartCandidate(
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        source_seq=source.source_seq,
        import_component_no=source.component_no,
        import_part_no=source.part_no,
        spec=spec,
        width=parent.normalized_width,
        cut_length=cut_length,
        material=source.material,
        child_quantity=source.original_qty,
        component_quantity=source.component_qty,
        part_type=parent.normalized_type,
        team=team,
        graphic="",
        file_value=rect.file_value,
        excluded=rect.exclude_from_part or parent.weight_validation_status == "severe_warning",
    )


def candidate_from_split(
    child: SplitPart,
    rect: RectDecision,
    *,
    cut_length: Decimal,
    team: str = "",
) -> PartCandidate:
    source = child.parent.source
    return PartCandidate(
        source_sheet=source.source_sheet,
        source_row=source.source_row,
        source_seq=source.source_seq,
        import_component_no=child.import_component_no,
        import_part_no=child.import_part_no,
        spec=child.spec,
        width=child.width,
        cut_length=cut_length,
        material=source.material,
        child_quantity=child.quantity,
        component_quantity=source.component_qty,
        part_type=child.part_type,
        team=team,
        graphic="",
        file_value=rect.file_value,
        excluded=rect.exclude_from_part or child.parent.weight_validation_status == "severe_warning",
    )


def _conflict_issue(candidates: list[PartCandidate]) -> QualityIssue:
    first = candidates[0]
    signatures = [
        (item.spec, item.width, item.cut_length, item.material, item.part_type)
        for item in candidates
    ]
    return QualityIssue(
        level=IssueLevel.SEVERE,
        category="导入零件身份冲突",
        source_sheet=first.source_sheet,
        source_row=first.source_row,
        component_no=first.import_component_no,
        part_no=first.import_part_no,
        spec=str(first.spec),
        field="导入零件号",
        actual_value=tuple(signatures),
        expected_value="同一构件内导入零件号的几何与材质唯一",
        absolute_error=None,
        relative_error=None,
        affects_part=True,
        density_source=None,
        description=(
            f"构件 {first.import_component_no} 的导入零件号 "
            f"{first.import_part_no} 对应多组几何或材质"
        ),
    )


def _sort_value(value: object) -> tuple[int, Decimal | str]:
    if value is None:
        return (0, "")
    try:
        return (1, Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return (2, str(value))


def build_part_rows(candidates: Iterable[PartCandidate]) -> PartBuildResult:
    source_candidates = [
        candidate
        for candidate in candidates
        if not candidate.excluded and candidate.part_type in TYPE_PRIORITY
    ]
    component_order: dict[str, int] = {}
    for candidate in source_candidates:
        component_order.setdefault(candidate.import_component_no, len(component_order))

    by_identity: dict[tuple[str, str], list[PartCandidate]] = {}
    for candidate in source_candidates:
        by_identity.setdefault(
            (candidate.import_component_no, candidate.import_part_no),
            [],
        ).append(candidate)
    conflicts: set[tuple[str, str]] = set()
    issues: list[QualityIssue] = []
    for identity, identity_candidates in by_identity.items():
        signatures = {
            (
                item.spec,
                item.width,
                item.cut_length,
                item.material,
                item.part_type,
            )
            for item in identity_candidates
        }
        if len(signatures) > 1:
            conflicts.add(identity)
            issues.append(_conflict_issue(identity_candidates))

    grouped: dict[tuple[object, ...], PartRow] = {}
    for candidate in source_candidates:
        identity = (candidate.import_component_no, candidate.import_part_no)
        if identity in conflicts:
            continue
        key = (
            candidate.import_component_no,
            candidate.import_part_no,
            candidate.spec,
            candidate.width,
            candidate.cut_length,
            candidate.material,
            candidate.part_type,
            candidate.team,
        )
        contribution = candidate.child_quantity * candidate.component_quantity
        current = grouped.get(key)
        if current is None:
            grouped[key] = PartRow(
                import_component_no=candidate.import_component_no,
                import_part_no=candidate.import_part_no,
                spec=candidate.spec,
                width=candidate.width,
                cut_length=candidate.cut_length,
                material=candidate.material,
                summary=contribution,
                team=candidate.team,
                graphic=candidate.graphic,
                part_type=candidate.part_type,
                file_value=candidate.file_value,
            )
        else:
            grouped[key] = replace(current, summary=current.summary + contribution)

    rows = sorted(
        grouped.values(),
        key=lambda row: (
            component_order[row.import_component_no],
            TYPE_PRIORITY[row.part_type],
            row.import_part_no,
            _sort_value(row.spec),
            _sort_value(row.width),
            _sort_value(row.cut_length),
            row.material,
        ),
    )
    return PartBuildResult(tuple(rows), tuple(issues))
