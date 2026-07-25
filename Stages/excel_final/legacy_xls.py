"""Small xlrd-backed adapter for legacy binary XLS workbooks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any, Iterator

import xlrd


def _cell_value(book: xlrd.book.Book, cell: xlrd.sheet.Cell) -> Any:
    if cell.ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}:
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        value = xlrd.xldate_as_datetime(cell.value, book.datemode)
        if value.time() == time():
            return value.date()
        return value
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return f"#XLS_ERROR_{int(cell.value)}"
    if cell.ctype == xlrd.XL_CELL_NUMBER and float(cell.value).is_integer():
        return int(cell.value)
    return cell.value


@dataclass(frozen=True, slots=True)
class LegacyCell:
    value: Any


class LegacySheet:
    def __init__(self, book: xlrd.book.Book, sheet: xlrd.sheet.Sheet) -> None:
        self.title = sheet.name
        self._rows = tuple(
            tuple(_cell_value(book, cell) for cell in sheet.row(row_number))
            for row_number in range(sheet.nrows)
        )
        self.max_row = len(self._rows)

    def iter_rows(
        self,
        *,
        min_row: int = 1,
        max_row: int | None = None,
        values_only: bool = False,
    ) -> Iterator[tuple[Any, ...] | tuple[LegacyCell, ...]]:
        end = self.max_row if max_row is None else min(max_row, self.max_row)
        for row in self._rows[max(0, min_row - 1):end]:
            if values_only:
                yield row
            else:
                yield tuple(LegacyCell(value) for value in row)

    def cell(self, *, row: int, column: int) -> LegacyCell:
        if row < 1 or column < 1:
            raise ValueError("row and column are one-based")
        try:
            value = self._rows[row - 1][column - 1]
        except IndexError:
            value = None
        return LegacyCell(value)


class LegacyWorkbook:
    def __init__(self, book: xlrd.book.Book) -> None:
        self._book = book
        self.worksheets = tuple(
            LegacySheet(book, book.sheet_by_index(index))
            for index in range(book.nsheets)
        )
        self.sheetnames = tuple(sheet.title for sheet in self.worksheets)

    def __getitem__(self, name: str) -> LegacySheet:
        for sheet in self.worksheets:
            if sheet.title == name:
                return sheet
        raise KeyError(name)

    def close(self) -> None:
        release = getattr(self._book, "release_resources", None)
        if release is not None:
            release()


def open_legacy_workbook(path: str | Path) -> LegacyWorkbook:
    return LegacyWorkbook(xlrd.open_workbook(str(Path(path)), on_demand=True))


__all__ = ["LegacyCell", "LegacySheet", "LegacyWorkbook", "open_legacy_workbook"]
