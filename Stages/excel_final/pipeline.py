"""Thin input adapters around the shared canonical Excel Final engine."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
import logging
import os
import tempfile
from typing import Iterator

from openpyxl import Workbook

import config as cfg
from canonical_pipeline import HandbookReader, process_canonical_records
from config import OUTPUT_DIR
from domain import ComponentRowKind, ComponentSourceRow, PipelineOutcome
from handbook import close_handbook, init_handbook
from reader import CanonicalWorkbookRead, read_canonical_source
from reader_init import read_init_canonical, read_init_table


log = logging.getLogger(__name__)


def _default_output(input_file: Path) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{input_file.stem}_处理后.xlsx"


def _xlsx_output(output_file: str | Path) -> Path:
    output_path = Path(output_file).resolve()
    if output_path.suffix.lower() != ".xlsx":
        raise ValueError("Excel Final output must use the .xlsx extension")
    return output_path


def _initial_component_rows(input_file: Path) -> tuple[ComponentSourceRow, ...]:
    component, _ = read_init_table(input_file)
    return (
        ComponentSourceRow(
            source_sheet="初始表",
            source_row=1,
            kind=ComponentRowKind.SUMMARY,
            batch=None,
            component_no=component.component_no.replace(" ", "").replace("　", ""),
            component_qty=Decimal(str(component.component_qty)),
            original_spec=None,
            material=None,
            source_unit_net=None,
            source_total_net=None,
            source_unit_gross=None,
            source_total_gross=None,
            source_unit_area=None,
            source_total_area=None,
            component_length=None,
            component_width=None,
            component_height=None,
        ),
    )


def _decode_tab_text(path: Path) -> list[list[object]] | None:
    for encoding in ("utf-8-sig", "gb18030", "gbk", "gb2312"):
        try:
            text = path.read_text(encoding=encoding)
        except UnicodeError:
            continue
        if "\t" not in text:
            return None
        return [
            [value if value != "" else None for value in line.split("\t")]
            for line in text.splitlines()
        ]
    return None


@contextmanager
def _writer_source(
    input_file: Path,
    output_file: Path,
    canonical_read: CanonicalWorkbookRead | None = None,
) -> Iterator[Path]:
    """Materialize Tekla text as one reviewed raw sheet for the workbook writer."""
    if input_file.suffix.lower() in {".xlsx", ".xlsm"}:
        yield input_file
        return

    output_file.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        prefix=f".{input_file.stem}.raw.", suffix=".xlsx", dir=output_file.parent
    )
    os.close(fd)
    Path(name).unlink(missing_ok=True)
    try:
        rows = _decode_tab_text(input_file)
        if rows is None:
            if canonical_read is None:
                raise ValueError("Tekla text source could not be materialized")
            rows = [list(row) for row in canonical_read.working_values]
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "原表"
        for row in rows:
            sheet.append(row)
        workbook.save(name)
        workbook.close()
        yield Path(name)
    finally:
        Path(name).unlink(missing_ok=True)


def _run_with_repository(
    *,
    source_path: Path,
    output_path: Path,
    parts,
    component_rows,
    reader_issues,
    handbook_repository: HandbookReader | None,
    internal_output_file: str | Path | None,
) -> PipelineOutcome:
    owned_repository = handbook_repository is None
    repository = handbook_repository or init_handbook(cfg.DB_CONFIG)
    try:
        return process_canonical_records(
            source_path,
            output_path,
            parts=parts,
            component_rows=component_rows,
            reader_issues=reader_issues,
            handbook=repository,
            internal_output_path=internal_output_file,
        )
    finally:
        repository.log_stats()
        if owned_repository:
            close_handbook()


def run_pipeline(
    input_file: str | Path,
    output_file: str | Path | None = None,
    *,
    handbook_repository: HandbookReader | None = None,
    internal_output_file: str | Path | None = None,
) -> PipelineOutcome:
    """Adapt one strict Tekla source and run the canonical engine."""
    input_path = Path(input_file).resolve()
    output_path = _xlsx_output(
        _default_output(input_path) if output_file is None else output_file
    )
    log.info("规范 Tekla 流程: %s → %s", input_path.name, output_path.name)
    canonical_read = read_canonical_source(input_path)
    with _writer_source(input_path, output_path, canonical_read) as source_path:
        return _run_with_repository(
            source_path=source_path,
            output_path=output_path,
            parts=canonical_read.parts,
            component_rows=canonical_read.component_rows,
            reader_issues=canonical_read.issues,
            handbook_repository=handbook_repository,
            internal_output_file=internal_output_file,
        )


def run_init_pipeline(
    input_file: str | Path,
    output_file: str | Path | None = None,
    *,
    handbook_repository: HandbookReader | None = None,
    internal_output_file: str | Path | None = None,
) -> PipelineOutcome:
    """Adapt one strict initial-table workbook and run the same canonical engine."""
    input_path = Path(input_file).resolve()
    output_path = _xlsx_output(
        _default_output(input_path) if output_file is None else output_file
    )
    log.info("规范初始表流程: %s → %s", input_path.name, output_path.name)
    parts = read_init_canonical(input_path)
    component_rows = _initial_component_rows(input_path)
    return _run_with_repository(
        source_path=input_path,
        output_path=output_path,
        parts=parts,
        component_rows=component_rows,
        reader_issues=(),
        handbook_repository=handbook_repository,
        internal_output_file=internal_output_file,
    )
