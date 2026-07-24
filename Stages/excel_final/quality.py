"""Structured quality issues and aggregate status for Excel Final."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
import re

from domain import PipelineOutcome


class IssueLevel(StrEnum):
    INFO = "信息"
    WARNING = "警告"
    SEVERE = "严重"
    FATAL = "致命"


class QualityStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    SEVERE_WARNING = "severe_warning"


_ACTION_BY_CATEGORY = {
    "五金手册查无": "补充对应手册数据，或确认该规格无需理论重后重新处理",
    "五金手册数据冲突": "核对唯一源手册中的冲突行，统一重量后重新生成数据库并处理",
    "手册查无": "补充对应手册数据，或确认该规格无需理论重后重新处理",
    "源重量缺失": "补齐涉及的源重量后重新处理",
    "关键字段缺失": "补齐涉及字段后重新处理",
    "物理量非法": "将涉及字段修正为合法有限数后重新处理",
    "构件物理量非法": "将涉及字段修正为合法有限数后重新处理",
    "构件编号冲突": "统一冲突的构件身份和数据后重新处理",
    "导入零件身份冲突": "统一冲突的零件身份和几何数据后重新处理",
    "拆板几何异常": "修正 BH/BOX/BT 截面尺寸后重新处理",
    "拆板重量守恒异常": "检查拆板规格和数量倍率后重新处理",
    "源重量链异常": "核对单重、总重、数量及净毛重关系后重新处理",
    "净重大于毛重": "核对单重、总重、数量及净毛重关系后重新处理",
    "几何理论重与毛重": "抽查轮廓、切割和毛坯口径；仅在源毛重用于下料或采购时人工确认",
    "手册理论重与毛重": "确认项目采用的型材标准版本；需要时补充版本映射后重新处理",
    "净重大于理论重": "确认模型轮廓、圆角和手册版本；源净重未超过毛重时可保留",
}
_DEFAULT_ACTION = "复核该问题并修正源数据后重新处理"
_ACTIONABLE_LEVELS = frozenset({IssueLevel.WARNING, IssueLevel.SEVERE, IssueLevel.FATAL})
_SECONDARY_WEIGHT_REVIEW_CATEGORIES = frozenset(
    {
        "几何理论重与毛重",
        "手册理论重与毛重",
        "净重大于理论重",
    }
)
_REPRESENTATIVE_LIMIT = 3
_FABRICATED_THEORY_BASIS = {
    "BH": "BH拆板合计父理论重（腹板×1+翼板×2）",
    "BOX": "BOX拆板合计父理论重（腹板×2+翼板×2）",
    "BT": "BT拆板合计父理论重（腹板×1+翼板×1）",
}


@dataclass(frozen=True, slots=True)
class QualityIssue:
    level: IssueLevel
    category: str
    source_sheet: str
    source_row: int
    component_no: str | None
    part_no: str | None
    spec: str | None
    field: str | None
    actual_value: object
    expected_value: object
    absolute_error: Decimal | None
    relative_error: Decimal | None
    affects_part: bool
    density_source: str | None
    description: str

    def __post_init__(self) -> None:
        must_isolate = self.level in (IssueLevel.SEVERE, IssueLevel.FATAL)
        if self.affects_part != must_isolate:
            raise ValueError(
                f"affects_part={self.affects_part!r} is inconsistent with level={self.level.value}"
            )


class QualityLedger:
    def __init__(self) -> None:
        self._issues: list[QualityIssue] = []

    def add(self, issue: QualityIssue) -> None:
        self._issues.append(issue)

    @property
    def issues(self) -> tuple[QualityIssue, ...]:
        return tuple(self._issues)

    @property
    def warning_count(self) -> int:
        return sum(row["级别"] == IssueLevel.WARNING.value for row in self.report_rows())

    @property
    def severe_warning_count(self) -> int:
        return sum(row["级别"] == IssueLevel.SEVERE.value for row in self.report_rows())

    @property
    def quality_status(self) -> QualityStatus:
        if any(issue.level in (IssueLevel.SEVERE, IssueLevel.FATAL) for issue in self._issues):
            return QualityStatus.SEVERE_WARNING
        if self.warning_count:
            return QualityStatus.WARNING
        return QualityStatus.OK

    def report_rows(self) -> list[dict[str, object]]:
        actionable = [issue for issue in self._issues if issue.level in _ACTIONABLE_LEVELS]
        severe_locations = {
            (issue.source_sheet, issue.source_row)
            for issue in actionable
            if issue.level in (IssueLevel.SEVERE, IssueLevel.FATAL)
        }
        grouped: dict[tuple[object, ...], list[QualityIssue]] = {}
        for issue in actionable:
            if (
                issue.level is IssueLevel.WARNING
                and issue.category in _SECONDARY_WEIGHT_REVIEW_CATEGORIES
                and (issue.source_sheet, issue.source_row) in severe_locations
            ):
                continue
            action = _ACTION_BY_CATEGORY.get(issue.category, _DEFAULT_ACTION)
            if issue.category == "几何理论重与毛重":
                key = (
                    issue.level,
                    issue.category,
                    issue.source_sheet,
                    _geometry_theory_basis(issue),
                    _comparison_direction(issue),
                    action,
                )
            else:
                key = (
                    issue.level,
                    issue.category,
                    issue.source_sheet,
                    issue.spec,
                    issue.density_source,
                    action,
                )
            grouped.setdefault(key, []).append(issue)

        rows: list[dict[str, object]] = []
        for issues in grouped.values():
            first = issues[0]
            source_rows = _unique((issue.source_sheet, issue.source_row) for issue in issues)
            if first.category == "几何理论重与毛重":
                direction = _comparison_direction(first)
                description = f"源毛重{direction}{_geometry_theory_basis(first)}"
                relative_errors = [
                    issue.relative_error
                    for issue in issues
                    if issue.relative_error is not None and issue.relative_error.is_finite()
                ]
                if relative_errors:
                    maximum = max(relative_errors) * Decimal("100")
                    description += f"；最大相对偏差 {maximum:.2f}%"
                if len(source_rows) > 1:
                    description = f"影响 {len(source_rows)} 行；{description}"
            else:
                descriptions = _unique(issue.description for issue in issues if issue.description)
                description = "；".join(descriptions[:_REPRESENTATIVE_LIMIT])
                if len(descriptions) > _REPRESENTATIVE_LIMIT:
                    description += f"；另有 {len(descriptions) - _REPRESENTATIVE_LIMIT} 种说明"
                if len(source_rows) > 1:
                    description = f"影响 {len(source_rows)} 行；{description}"
            rows.append(
                {
                    "级别": first.level.value,
                    "类别": first.category,
                    "来源位置": _source_representatives(source_rows),
                    "构件编号": _value_representatives(issue.component_no for issue in issues),
                    "零件号": _value_representatives(issue.part_no for issue in issues),
                    "涉及字段": "；".join(_unique(issue.field for issue in issues if issue.field)),
                    "说明": description,
                    "建议操作": _ACTION_BY_CATEGORY.get(
                        first.category,
                        _DEFAULT_ACTION,
                    ),
                }
            )
        return rows

    def to_outcome(self, output_path: Path) -> PipelineOutcome:
        report_rows = self.report_rows()
        category_counts = Counter(str(row["类别"]) for row in report_rows)
        summary: dict[str, object] = {
            "info_count": 0,
            "warning_count": self.warning_count,
            "severe_warning_count": self.severe_warning_count,
            "category_counts": dict(category_counts),
            "representative_messages": [str(row["说明"]) for row in report_rows[:10]],
        }
        return PipelineOutcome(
            output_path=output_path,
            quality_status=self.quality_status.value,
            warning_count=self.warning_count,
            severe_warning_count=self.severe_warning_count,
            report_summary=summary,
        )


def _unique(values):
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _comparison_direction(issue: QualityIssue) -> str:
    try:
        actual = Decimal(str(issue.actual_value))
        expected = Decimal(str(issue.expected_value))
    except (InvalidOperation, TypeError, ValueError):
        return "偏离"
    if actual > expected:
        return "高于"
    if actual < expected:
        return "低于"
    return "等于"


def _geometry_theory_basis(issue: QualityIssue) -> str:
    compact = re.sub(r"\s+", "", str(issue.spec or "")).upper()
    for prefix, basis in _FABRICATED_THEORY_BASIS.items():
        if compact.startswith(prefix):
            return basis
    return "几何理论重"


def _source_representatives(
    source_rows: list[tuple[str, int]],
) -> str:
    sheets = _unique(sheet for sheet, _ in source_rows)
    if len(sheets) == 1:
        result = f"{sheets[0]}!" + "、".join(
            str(row) for _, row in source_rows[:_REPRESENTATIVE_LIMIT]
        )
    else:
        result = "、".join(f"{sheet}!{row}" for sheet, row in source_rows[:_REPRESENTATIVE_LIMIT])
    if len(source_rows) > _REPRESENTATIVE_LIMIT:
        result += f" 等 {len(source_rows)} 行"
    return result


def _value_representatives(values) -> str | None:
    unique = _unique(str(value) for value in values if value not in (None, ""))
    if not unique:
        return None
    result = "、".join(unique[:_REPRESENTATIVE_LIMIT])
    if len(unique) > _REPRESENTATIVE_LIMIT:
        result += f" 等 {len(unique)} 个"
    return result
