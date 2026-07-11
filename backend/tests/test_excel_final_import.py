from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.constants import TASK_EXCEL_FINAL
from app.models.excel_final import ExcelFinalBatch, ExcelFinalPart
from app.models.file import StoredFile
from app.models.job import Job
from app.services.excel_final_service import _import_parts_to_db


def _batch(db: Session) -> ExcelFinalBatch:
    source = StoredFile(
        bucket="dwg-reports",
        storage_key="uploads/source.xls",
        original_name="source.xls",
        file_ext=".xls",
        content_type="application/vnd.ms-excel",
        size_bytes=10,
        sha256="b" * 64,
        status="available",
    )
    db.add(source)
    db.flush()
    job = Job(
        task_type=TASK_EXCEL_FINAL,
        precision_level="normal",
        pipeline="excel_final",
        status="running",
        priority=0,
        progress=60,
        params_json={"file_id": source.id},
    )
    db.add(job)
    db.flush()
    batch = ExcelFinalBatch(
        job_id=job.id,
        file_id=source.id,
        source_type="tsv",
        source_name="source.xls",
    )
    db.add(batch)
    db.flush()
    return batch


def _workbook(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "整理表"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    workbook.save(path)


def test_excel_final_import_skips_totals_row(db: Session, tmp_path: Path):
    batch = _batch(db)
    output_path = tmp_path / "result.xlsx"
    headers = ["序号", "构件编号", "零件号", "规格", "长度(mm)", "材质", "数量"]
    _workbook(
        output_path,
        headers,
        [
            [1, "C-1", "P-1", "PL10*100", 200, "Q355B", 2],
            [2, "C-1", "1.00", "合计：", 200, "123.45", None],
        ],
    )

    stats = _import_parts_to_db(db, batch.id, output_path)

    assert stats == {"parts_imported": 1}
    assert db.scalar(select(func.count()).select_from(ExcelFinalPart)) == 1
    part = db.scalar(select(ExcelFinalPart))
    assert part is not None
    assert part.part_no == "P-1"
    assert part.material == "Q355B"


def test_excel_final_import_rejects_missing_core_headers(db: Session, tmp_path: Path):
    batch = _batch(db)
    output_path = tmp_path / "invalid.xlsx"
    _workbook(output_path, ["序号", "零件号"], [[1, "P-1"]])

    with pytest.raises(ValueError, match="missing required columns"):
        _import_parts_to_db(db, batch.id, output_path)
