"""Structured quality issues and aggregate status for Excel Final."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

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
    "手册查无": "补充对应手册数据，或确认该规格无需理论重后重新处理",
    "源重量缺失": "补齐涉及的源重量后重新处理",
    "关键字段缺失": "补齐涉及字段后重新处理",
    "物理量非法": "将涉及字段修正为合法有限数后重新处理",
    "构件物理量非法": "将涉及字段修正为合法有限数后重新处理",
    "构件编号冲突": "统一冲突的构件身份和数据后重新处理",
    "导入零件身份冲突": "统一冲突的零件身份和几何数据后重新处理",
    "拆板几何异常": "修正 BH/BOX/BT 截面尺寸后重新处理",
    "源重量链异常": "核对单重、总重、数量及净毛重关系后重新处理",
    "净重大于毛重": "核对单重、总重、数量及净毛重关系后重新处理",
    "几何理论重与毛重": "复核规格、材质、数量和源重量，修正后重新处理",
    "手册理论重与毛重": "复核规格、材质、数量和源重量，修正后重新处理",
    "净重大于理论重": "复核规格、材质、数量和源重量，修正后重新处理",
}
_DEFAULT_ACTION = "复核该问题并修正源数据后重新处理"
_ACTIONABLE_LEVELS = frozenset(
    {IssueLevel.WARNING, IssueLevel.SEVERE, IssueLevel.FATAL}
)


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
        grouped: dict[tuple[object, ...], dict[str, object]] = {}
        for issue in self._issues:
            if issue.level not in _ACTIONABLE_LEVELS:
                continue
            key = (
                issue.level,
                issue.category,
                issue.source_sheet,
                issue.source_row,
                issue.component_no,
                issue.part_no,
            )
            row = grouped.setdefault(
                key,
                {
                    "级别": issue.level.value,
                    "类别": issue.category,
                    "来源位置": f"{issue.source_sheet}!{issue.source_row}",
                    "构件编号": issue.component_no,
                    "零件号": issue.part_no,
                    "涉及字段": [],
                    "说明": [],
                    "建议操作": _ACTION_BY_CATEGORY.get(
                        issue.category,
                        _DEFAULT_ACTION,
                    ),
                },
            )
            fields = row["涉及字段"]
            descriptions = row["说明"]
            if issue.field and issue.field not in fields:
                fields.append(issue.field)
            if issue.description and issue.description not in descriptions:
                descriptions.append(issue.description)

        rows: list[dict[str, object]] = []
        for grouped_row in grouped.values():
            row = dict(grouped_row)
            row["涉及字段"] = "；".join(grouped_row["涉及字段"])
            row["说明"] = "；".join(grouped_row["说明"])
            rows.append(row)
        return rows

    def to_outcome(self, output_path: Path) -> PipelineOutcome:
        report_rows = self.report_rows()
        category_counts = Counter(str(row["类别"]) for row in report_rows)
        summary: dict[str, object] = {
            "info_count": 0,
            "warning_count": self.warning_count,
            "severe_warning_count": self.severe_warning_count,
            "category_counts": dict(category_counts),
            "representative_messages": [
                str(row["说明"]) for row in report_rows[:10]
            ],
        }
        return PipelineOutcome(
            output_path=output_path,
            quality_status=self.quality_status.value,
            warning_count=self.warning_count,
            severe_warning_count=self.severe_warning_count,
            report_summary=summary,
        )
