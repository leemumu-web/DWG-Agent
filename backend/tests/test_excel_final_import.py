from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.excel_processing import importers
from app.modules.excel_processing.importers import (
    import_components_to_db,
    import_parts_to_db,
)
from app.modules.excel_processing.models import (
    ExcelFinalBatch,
    ExcelFinalComponent,
    ExcelFinalPart,
)
from app.modules.files.interface import StoredFile
from app.modules.jobs.interface import Job
from app.platform.config.constants import TASK_EXCEL_FINAL


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


def _workbook(
    path: Path,
    headers: list[str],
    rows: list[list[object]],
    *,
    sheet_name: str = "整理表",
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
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

    stats = import_parts_to_db(db, batch.id, output_path)

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
        import_parts_to_db(db, batch.id, output_path)


def test_excel_final_parts_imports_sparse_rows_and_optional_fields(db: Session, tmp_path: Path):
    batch = _batch(db)
    output_path = tmp_path / "sparse.xlsx"
    _workbook(
        output_path,
        ["序号", "构件编号", "零件号", "截面型材", "规格", "长度(mm)", "材质", "数量"],
        [["2", " C-2 ", " P-2 ", None, "PL8*80", "120.5", " Q235B ", "0"]],
    )

    stats = import_parts_to_db(db, batch.id, output_path)

    assert stats == {"parts_imported": 1}
    part = db.scalar(select(ExcelFinalPart))
    assert part is not None
    assert part.seq == 2
    assert part.component_no == "C-2"
    assert part.part_no == "P-2"
    assert part.profile_spec is None
    assert part.length == 120.5
    assert part.material == "Q235B"
    assert part.qty == 0


def test_excel_final_parts_uses_intermediate_sheet_fallback(db: Session, tmp_path: Path):
    batch = _batch(db)
    output_path = tmp_path / "intermediate.xlsx"
    _workbook(
        output_path,
        ["序号", "构件编号", "零件号", "规格", "长度", "材质", "数量"],
        [[1, "C-1", "P-1", "PL10*100", 200, "Q355B", 2]],
        sheet_name="整理表_拆板后",
    )

    assert import_parts_to_db(db, batch.id, output_path) == {"parts_imported": 1}


def test_excel_final_parts_returns_error_when_sheet_is_absent(db: Session, tmp_path: Path):
    batch = _batch(db)
    output_path = tmp_path / "missing-sheet.xlsx"
    _workbook(output_path, ["other"], [], sheet_name="Sheet1")

    assert import_parts_to_db(db, batch.id, output_path) == {
        "parts_imported": 0,
        "error": "No 整理表 sheet found",
    }


def test_excel_final_components_imports_rows_and_preserves_zero_qty(
    db: Session,
    tmp_path: Path,
):
    batch = _batch(db)
    output_path = tmp_path / "components.xlsx"
    _workbook(
        output_path,
        ["序号", "批次", "构件编号", "构件数", "总净重"],
        [
            [1, "B-1", "C-1", 2, 123.5],
            [2, "B-2", "C-2", 0, 0],
            [3, "", "合计", 2, 123.5],
            [4, "", " ", 1, 20],
        ],
        sheet_name="构件表",
    )

    stats = import_components_to_db(db, batch.id, output_path)

    assert stats == {"components_imported": 2}
    components = list(db.scalars(select(ExcelFinalComponent).order_by(ExcelFinalComponent.id)))
    assert [(item.component_no, item.component_qty, item.total_weight) for item in components] == [
        ("C-1", 2, 123.5),
        ("C-2", 0, 0),
    ]


def test_excel_final_components_allows_missing_optional_columns(db: Session, tmp_path: Path):
    batch = _batch(db)
    output_path = tmp_path / "component-number-only.xlsx"
    _workbook(
        output_path,
        ["构件编号"],
        [["C-1"]],
        sheet_name="构件表",
    )

    stats = import_components_to_db(db, batch.id, output_path)

    assert stats == {"components_imported": 1}
    component = db.scalar(select(ExcelFinalComponent))
    assert component is not None
    assert component.component_no == "C-1"
    assert component.component_qty is None
    assert component.total_weight is None


def test_excel_final_components_rejects_missing_component_number(
    db: Session,
    tmp_path: Path,
):
    batch = _batch(db)
    output_path = tmp_path / "invalid-components.xlsx"
    _workbook(
        output_path,
        ["构件数", "总重"],
        [[2, 123.5]],
        sheet_name="构件表",
    )

    with pytest.raises(ValueError, match="missing required column: 构件编号"):
        import_components_to_db(db, batch.id, output_path)


def test_excel_final_components_returns_zero_when_sheet_is_absent(
    db: Session,
    tmp_path: Path,
):
    batch = _batch(db)
    output_path = tmp_path / "no-components.xlsx"
    _workbook(output_path, ["序号"], [], sheet_name="整理表")

    assert import_components_to_db(db, batch.id, output_path) == {"components_imported": 0}


def test_excel_final_importers_never_use_random_cell_access(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "streaming.xlsx"
    workbook = Workbook()
    parts_sheet = workbook.active
    parts_sheet.title = "整理表"
    parts_sheet.append(["序号", "构件编号", "零件号", "规格", "长度", "材质", "数量"])
    parts_sheet.append([1, "C-1", "P-1", "PL10*100", 200, "Q355B", 2])
    component_sheet = workbook.create_sheet("构件表")
    component_sheet.append(["构件编号", "构件数", "总净重"])
    component_sheet.append(["C-1", 1, 123.5])
    workbook.save(output_path)

    real_load_workbook = importers.openpyxl.load_workbook

    def guarded_load_workbook(*args, **kwargs):
        loaded = real_load_workbook(*args, **kwargs)
        for sheet in loaded.worksheets:
            sheet.cell = Mock(side_effect=AssertionError("random worksheet access is forbidden"))
        return loaded

    monkeypatch.setattr(importers.openpyxl, "load_workbook", guarded_load_workbook)
    db = Mock()

    assert import_parts_to_db(db, 1, output_path) == {"parts_imported": 1}
    assert import_components_to_db(db, 1, output_path) == {"components_imported": 1}
