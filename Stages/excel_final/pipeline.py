"""Thin input adapters around the shared canonical Excel Final engine."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import logging
import os
import tempfile
from typing import Iterator

from openpyxl import Workbook

import config as cfg
from canonical_pipeline import HandbookReader, process_canonical_records
from config import OUTPUT_DIR
from domain import PipelineOutcome
from handbook import close_handbook, init_handbook
from source_intake import SourceFormat, SourceIntakeResult, read_production_source


log = logging.getLogger(__name__)


def _default_output(input_file: Path) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR / f"{input_file.stem}_处理后.xlsx"


def _xlsx_output(output_file: str | Path) -> Path:
    output_path = Path(output_file).resolve()
    if output_path.suffix.lower() != ".xlsx":
        raise ValueError("Excel Final output must use the .xlsx extension")
    return output_path


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
    intake: SourceIntakeResult | None = None,
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
            if intake is None:
                raise ValueError("Tekla text source could not be materialized")
            rows = [list(row) for row in intake.working_values]
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


def _run_intake(
    intake: SourceIntakeResult,
    output_file: str | Path | None,
    *,
    handbook_repository: HandbookReader | None,
    internal_output_file: str | Path | None,
) -> PipelineOutcome:
    input_path = intake.source_path
    output_path = _xlsx_output(
        _default_output(input_path) if output_file is None else output_file
    )
    log.info(
        "规范 Excel Final 流程 [%s]: %s → %s",
        intake.source_format.value,
        input_path.name,
        output_path.name,
    )
    with _writer_source(input_path, output_path, intake) as source_path:
        return _run_with_repository(
            source_path=source_path,
            output_path=output_path,
            parts=intake.parts,
            component_rows=intake.component_rows,
            reader_issues=intake.issues,
            handbook_repository=handbook_repository,
            internal_output_file=internal_output_file,
        )


def run_auto_pipeline(
    input_file: str | Path,
    output_file: str | Path | None = None,
    *,
    handbook_repository: HandbookReader | None = None,
    internal_output_file: str | Path | None = None,
) -> PipelineOutcome:
    """Auto-detect one supported production source and run the canonical engine."""
    return _run_intake(
        read_production_source(input_file),
        output_file,
        handbook_repository=handbook_repository,
        internal_output_file=internal_output_file,
    )


def run_pipeline(
    input_file: str | Path,
    output_file: str | Path | None = None,
    *,
    handbook_repository: HandbookReader | None = None,
    internal_output_file: str | Path | None = None,
) -> PipelineOutcome:
    """Compatibility wrapper that requires a Tekla source."""
    intake = read_production_source(input_file)
    if intake.source_format is SourceFormat.INITIAL_WORKBOOK:
        raise ValueError("Tekla pipeline received an initial-table workbook")
    return _run_intake(
        intake,
        output_file,
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
    """Compatibility wrapper that requires an initial-table workbook."""
    intake = read_production_source(input_file)
    if intake.source_format is not SourceFormat.INITIAL_WORKBOOK:
        raise ValueError("initial-table pipeline received a Tekla source")
    return _run_intake(
        intake,
        output_file,
        handbook_repository=handbook_repository,
        internal_output_file=internal_output_file,
    )
