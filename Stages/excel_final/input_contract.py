"""Strict production-input boundary for Excel Final."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from openpyxl import load_workbook


class InputContractError(ValueError):
    """Raised when a production source cannot be interpreted unambiguously."""


class InputKind(StrEnum):
    WORKBOOK = "workbook"
    TEKLA_TEXT = "tekla_text"


@dataclass(frozen=True, slots=True)
class ProductionInput:
    path: Path
    kind: InputKind
    sheet_name: str | None


@dataclass(frozen=True, slots=True)
class HeaderDetection:
    row_number: int
    columns: Mapping[str, int]
    candidate_scores: tuple[HeaderCandidateScore, ...]


@dataclass(frozen=True, slots=True)
class HeaderCandidateScore:
    row_number: int
    score: int
    missing: tuple[str, ...]
    conflicts: tuple[str, ...] = ()


_HEADER_ALIASES = {
    "批次": frozenset({"批次"}),
    "构件编号": frozenset({"构件编号", "构件号"}),
    "零件号": frozenset({"零件号", "零件编号"}),
    "规格": frozenset({"规格", "型材", "截面型材"}),
    "材质": frozenset({"材质"}),
    "数量": frozenset({"数量"}),
    "单净重": frozenset({"单净重"}),
    "总净重": frozenset({"总净重"}),
    "单毛重": frozenset({"单毛重", "单重"}),
    "总毛重": frozenset({"总毛重", "总重"}),
    "单表面积": frozenset({"单表面积", "单面积", "单涂装面积"}),
    "总表面积": frozenset({"总表面积", "总面积", "总涂装面积"}),
    "版本": frozenset({"版本"}),
}
_ALIAS_TO_HEADER = {
    alias: canonical
    for canonical, aliases in _HEADER_ALIASES.items()
    for alias in aliases
}

_REQUIRED_HEADERS = (
    "构件编号",
    "零件号",
    "规格",
    "零件长度",
    "材质",
    "数量",
)


def _normalized_header(value: Any) -> str:
    if value is None:
        return ""
    compact = "".join(str(value).split())
    return re.sub(r"[（(][^）)]*[）)]", "", compact)


def _columns_for_header_row(
    values: tuple[Any, ...],
) -> tuple[dict[str, int], tuple[str, ...]]:
    normalized = [_normalized_header(value) for value in values]
    matches: dict[str, list[int]] = {}
    for index, value in enumerate(normalized, start=1):
        canonical = _ALIAS_TO_HEADER.get(value)
        if canonical is not None:
            matches.setdefault(canonical, []).append(index)
    columns = {
        canonical: indexes[0]
        for canonical, indexes in matches.items()
    }
    conflicts = tuple(
        f"{canonical} columns={indexes}"
        for canonical, indexes in matches.items()
        if len(indexes) > 1
    )

    length_columns = [index for index, value in enumerate(normalized, start=1) if value == "长度"]
    if length_columns:
        columns["零件长度"] = length_columns[0]
        for index in length_columns[1:]:
            if normalized[index:index + 2] == ["宽度", "高度"]:
                columns["构件长度"] = index
                columns["构件宽度"] = index + 1
                columns["构件高度"] = index + 2
                break
    return columns, conflicts


def _candidate_diagnostics(candidates: list[tuple[HeaderCandidateScore, dict[str, int]]]) -> str:
    details = [
        (
            f"row={candidate.row_number} score={candidate.score} "
            f"missing={list(candidate.missing)} conflicts={list(candidate.conflicts)}"
        )
        for candidate, _ in candidates[:15]
    ]
    return "first 15 candidate scores: " + "; ".join(details)


def detect_canonical_header(worksheet: Any) -> HeaderDetection:
    """Locate the strongest canonical header without assuming a fixed row."""
    candidates: list[tuple[HeaderCandidateScore, dict[str, int]]] = []
    for row_number, values in enumerate(
        worksheet.iter_rows(min_row=1, max_row=min(worksheet.max_row, 100), values_only=True),
        start=1,
    ):
        columns, conflicts = _columns_for_header_row(values)
        missing = tuple(field for field in _REQUIRED_HEADERS if field not in columns)
        candidates.append((
            HeaderCandidateScore(row_number, len(columns), missing, conflicts),
            columns,
        ))
    if not candidates:
        raise InputContractError("worksheet does not contain a detectable header")

    valid = [
        (candidate, columns)
        for candidate, columns in candidates
        if not candidate.missing and not candidate.conflicts
    ]
    diagnostics = _candidate_diagnostics(candidates)
    if not valid:
        best = max(candidates, key=lambda item: item[0].score)[0]
        if best.conflicts:
            raise InputContractError(
                f"conflicting header aliases: {list(best.conflicts)}; {diagnostics}"
            )
        if best.missing == ("零件号",):
            raise InputContractError(
                "输入只有构件汇总，没有零件明细，不能生成 Excel Final part"
            )
        raise InputContractError(
            f"missing required fields: {list(best.missing)}; {diagnostics}"
        )

    best_score = max(candidate.score for candidate, _ in valid)
    winners = [(candidate, columns) for candidate, columns in valid if candidate.score == best_score]
    if len(winners) != 1:
        rows = [candidate.row_number for candidate, _ in winners]
        raise InputContractError(
            f"ambiguous canonical header at rows {rows}; {diagnostics}"
        )

    winner, columns = winners[0]
    scores = tuple(candidate for candidate, _ in candidates)
    return HeaderDetection(winner.row_number, MappingProxyType(columns), scores)


def inspect_production_input(path: Path) -> ProductionInput:
    resolved = path.resolve()
    if not resolved.is_file():
        raise InputContractError(f"production input does not exist: {resolved}")

    suffix = resolved.suffix.lower()
    if suffix == ".xls":
        return ProductionInput(resolved, InputKind.TEKLA_TEXT, None)
    if suffix not in {".xlsx", ".xlsm"}:
        raise InputContractError(f"unsupported production input extension: {suffix or '<none>'}")

    workbook = load_workbook(resolved, read_only=True, data_only=False)
    try:
        if len(workbook.sheetnames) != 1:
            raise InputContractError(
                "production workbook must contain exactly one worksheet; "
                f"found {len(workbook.sheetnames)}: {workbook.sheetnames}"
            )
        sheet_name = workbook.sheetnames[0]
    finally:
        workbook.close()
    return ProductionInput(resolved, InputKind.WORKBOOK, sheet_name)
