"""Per-component part eligibility, conflict detection, and projection."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Iterable

from domain import ParentPartEvidence, SplitPart
from quality import IssueLevel, QualityIssue

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
COMPONENT_SCOPED_TYPES = frozenset({
    "BH腹",
    "BH翼",
    "BOX腹",
    "BOX翼",
    "BT腹",
    "BT翼",
})
GLOBAL_SCOPED_TYPES = frozenset({"扁钢", "板材"})


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


@dataclass(frozen=True, slots=True)
class PartBuildResult:
    rows: tuple[PartRow, ...]
    issues: tuple[QualityIssue, ...]


def candidate_from_parent(
    parent: ParentPartEvidence,
    *,
    cut_length: Decimal,
    identity_consistent: bool,
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
        excluded=(
            not identity_consistent
            or parent.weight_validation_status == "severe_warning"
        ),
    )


def candidate_from_split(
    child: SplitPart,
    *,
    cut_length: Decimal,
    identity_consistent: bool,
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
        excluded=(
            not identity_consistent
            or child.parent.weight_validation_status == "severe_warning"
        ),
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


def _is_component_scoped(part_type: str) -> bool:
    if part_type in COMPONENT_SCOPED_TYPES:
        return True
    if part_type in GLOBAL_SCOPED_TYPES:
        return False
    raise ValueError(f"part type has no aggregation scope: {part_type}")


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
        if _is_component_scoped(candidate.part_type):
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
        output_component_no = (
            candidate.import_component_no
            if _is_component_scoped(candidate.part_type)
            else ""
        )
        key = (
            output_component_no,
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
                import_component_no=output_component_no,
                import_part_no=candidate.import_part_no,
                spec=candidate.spec,
                width=candidate.width,
                cut_length=candidate.cut_length,
                material=candidate.material,
                summary=contribution,
                team=candidate.team,
                graphic=candidate.graphic,
                part_type=candidate.part_type,
            )
        else:
            grouped[key] = replace(current, summary=current.summary + contribution)

    rows = sorted(
        grouped.values(),
        key=lambda row: (
            0 if _is_component_scoped(row.part_type) else 1,
            component_order.get(row.import_component_no, len(component_order)),
            TYPE_PRIORITY[row.part_type],
            row.import_part_no,
            _sort_value(row.spec),
            _sort_value(row.width),
            _sort_value(row.cut_length),
            row.material,
        ),
    )
    return PartBuildResult(tuple(rows), tuple(issues))
