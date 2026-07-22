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

    def as_report_row(self) -> dict[str, object]:
        return {
            "级别": self.level.value,
            "类别": self.category,
            "来源sheet": self.source_sheet,
            "来源行": self.source_row,
            "构件编号": self.component_no,
            "零件号": self.part_no,
            "规格": self.spec,
            "字段": self.field,
            "实际值": self.actual_value,
            "期望值": self.expected_value,
            "绝对误差": self.absolute_error,
            "相对误差": self.relative_error,
            "是否影响part": "是" if self.affects_part else "否",
            "比重来源": self.density_source,
            "说明": self.description,
        }


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
        return sum(issue.level is IssueLevel.WARNING for issue in self._issues)

    @property
    def severe_warning_count(self) -> int:
        return sum(issue.level is IssueLevel.SEVERE for issue in self._issues)

    @property
    def quality_status(self) -> QualityStatus:
        if any(issue.level in (IssueLevel.SEVERE, IssueLevel.FATAL) for issue in self._issues):
            return QualityStatus.SEVERE_WARNING
        if self.warning_count:
            return QualityStatus.WARNING
        return QualityStatus.OK

    def report_rows(self) -> list[dict[str, object]]:
        return [issue.as_report_row() for issue in self._issues]

    def to_outcome(self, output_path: Path) -> PipelineOutcome:
        category_counts = Counter(issue.category for issue in self._issues)
        summary: dict[str, object] = {
            "info_count": sum(issue.level is IssueLevel.INFO for issue in self._issues),
            "warning_count": self.warning_count,
            "severe_warning_count": self.severe_warning_count,
            "category_counts": dict(category_counts),
            "representative_messages": [
                issue.description for issue in self._issues[:10]
            ],
        }
        return PipelineOutcome(
            output_path=output_path,
            quality_status=self.quality_status.value,
            warning_count=self.warning_count,
            severe_warning_count=self.severe_warning_count,
            report_summary=summary,
        )
