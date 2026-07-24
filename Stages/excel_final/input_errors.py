"""Versioned, bounded operator-facing failures for Excel production input."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Mapping

INPUT_CONTRACT_VERSION = 1
MAX_INPUT_ISSUES = 20
MAX_INPUT_SHEETS = 10
MAX_DISPLAY_VALUE_LENGTH = 160


def _display_value(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r", " ").replace("\n", " ")
    if len(text) <= MAX_DISPLAY_VALUE_LENGTH:
        return text
    return f"{text[: MAX_DISPLAY_VALUE_LENGTH - 1]}…"


@dataclass(frozen=True, slots=True)
class ExcelInputIssue:
    sheet: str | None = None
    row: int | None = None
    column: str | None = None
    field: str | None = None
    value: str | None = None
    reason: str = ""

    @classmethod
    def create(
        cls,
        *,
        sheet: str | None = None,
        row: int | None = None,
        column: str | None = None,
        field: str | None = None,
        value: object | None = None,
        reason: str,
    ) -> ExcelInputIssue:
        return cls(
            sheet=sheet,
            row=row,
            column=column,
            field=field,
            value=_display_value(value),
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class ExcelInputFailure:
    code: str
    message: str
    action: str
    issues: tuple[ExcelInputIssue, ...] = ()
    sheets: tuple[str, ...] = ()
    meta: Mapping[str, object] = field(default_factory=dict)
    contract_version: int = INPUT_CONTRACT_VERSION

    def __post_init__(self) -> None:
        safe_meta = dict(self.meta)
        safe_meta.setdefault("issue_count", len(self.issues))
        safe_meta.setdefault("issues_truncated", len(self.issues) > MAX_INPUT_ISSUES)
        safe_meta.setdefault("sheet_count", len(self.sheets))
        safe_meta.setdefault("sheets_truncated", len(self.sheets) > MAX_INPUT_SHEETS)
        object.__setattr__(self, "meta", MappingProxyType(safe_meta))

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "action": self.action,
            "contract_version": self.contract_version,
            "issues": [
                asdict(issue) for issue in self.issues[:MAX_INPUT_ISSUES]
            ],
            "sheets": list(self.sheets[:MAX_INPUT_SHEETS]),
            "meta": dict(self.meta),
        }


class InputContractError(ValueError):
    """A safe operator failure plus a compatibility diagnostic for local logs."""

    def __init__(
        self,
        failure: ExcelInputFailure,
        *,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(diagnostic or failure.message)
        self.failure = failure


def input_failure(
    code: str,
    message: str,
    action: str,
    *,
    issues: tuple[ExcelInputIssue, ...] = (),
    sheets: tuple[str, ...] = (),
    meta: Mapping[str, object] | None = None,
) -> ExcelInputFailure:
    return ExcelInputFailure(
        code=code,
        message=message,
        action=action,
        issues=issues,
        sheets=sheets,
        meta=meta or {},
    )


__all__ = [
    "ExcelInputFailure",
    "ExcelInputIssue",
    "INPUT_CONTRACT_VERSION",
    "InputContractError",
    "input_failure",
]
