from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from source_intake import SourceFormat, read_production_source


def _standard_workbook(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "原表"
    sheet.append(["构件零件清单"])
    sheet.append([
        "构件编号",
        "零件号",
        "规格",
        "长度(mm)",
        "材质",
        "数量",
        "单毛重(kg)",
        "总毛重(kg)",
    ])
    sheet.append(["C1", None, "BH500*300*12*20", 1000, "Q355B", 2])
    sheet.append([None, "P1", "PL10*200", 100, "Q355B", 3, 1.57, 4.71])
    sheet.append(["C1", "构件小计", None, None, None, 2, 1.57, 3.14])
    workbook.save(path)
    workbook.close()
    return path


def _initial_workbook(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "初始表"
    sheet.append(["C2材  料  表构件数量：4构件总重：20.0"])
    sheet.append([
        "零件号",
        "截面型材",
        "长度(mm)",
        "材质",
        "数量",
        "单重(kg)",
        "总重(kg)",
        "总面积(m2)",
        "备注",
    ])
    sheet.append(["P2", "PL8*100", 200, "Q355B", 2, 1.26, 2.52, 0.1, None])
    sheet.append(["合计"])
    workbook.save(path)
    workbook.close()
    return path


def test_source_intake_detects_standard_workbook(tmp_path: Path) -> None:
    source = _standard_workbook(tmp_path / "standard.xlsx")

    result = read_production_source(source)

    assert result.source_path == source.resolve()
    assert result.source_format is SourceFormat.STANDARD_WORKBOOK
    assert result.sheet_name == "原表"
    assert len(result.parts) == 1
    assert result.parts[0].component_no == "C1"
    assert len(result.component_rows) == 1
    assert result.component_rows[0].component_qty == Decimal("2")


def test_source_intake_detects_initial_workbook(tmp_path: Path) -> None:
    source = _initial_workbook(tmp_path / "initial.xlsx")

    result = read_production_source(source)

    assert result.source_path == source.resolve()
    assert result.source_format is SourceFormat.INITIAL_WORKBOOK
    assert result.sheet_name == "初始表"
    assert len(result.parts) == 1
    assert result.parts[0].component_no == "C2"
    assert len(result.component_rows) == 1
    assert result.component_rows[0].component_qty == Decimal("4")


def test_initial_metadata_and_header_can_move(tmp_path: Path) -> None:
    source = tmp_path / "moved-initial.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "材料表"
    sheet.append(["项目说明"])
    sheet.append([])
    sheet.append(["C3材  料  表构件数量：3构件总重：30.0"])
    sheet.append([])
    sheet.append([
        "零件号",
        "截面型材",
        "长度(mm)",
        "材质",
        "数量",
        "单重(kg)",
        "总重(kg)",
        "总面积(m2)",
        "备注",
    ])
    sheet.append(["P3", "PL10*100", 300, "Q355B", 2, 2.36, 4.72, 0.2, None])
    sheet.append(["合计"])
    workbook.save(source)
    workbook.close()

    result = read_production_source(source)

    assert result.source_format is SourceFormat.INITIAL_WORKBOOK
    assert result.parts[0].source_row == 6
    assert result.parts[0].component_no == "C3"
    assert result.component_rows[0].source_row == 3
    assert result.component_rows[0].component_qty == Decimal("3")


def test_fixed_width_blank_spec_does_not_shift_bolt_columns(tmp_path: Path) -> None:
    source = tmp_path / "fixed-width.xls"
    source.write_text(
        "构件编号             零件编号       型 材                     构件名称        材   质       长度(mm)     数量     单净重(kg)  总净重(kg)    单毛重(kg)  总毛重(kg)   单面积(m2)  总面积(m2)    备 注\n"
        "SKG-C-WGKL-27                       PL80*1200                albl_Top_f       Q345GJB-      2335        1         4384.47      4384.47      4625.72      4625.72      26.34      26.34\n"
        "                     M22                                                      TS10.9         60         10        0.3          3.2\n",
        encoding="utf-8",
    )

    result = read_production_source(source)

    assert result.source_format is SourceFormat.FIXED_WIDTH_TEKLA_TEXT
    assert len(result.parts) == 1
    part = result.parts[0]
    assert part.part_no == "M22"
    assert part.original_spec == ""
    assert part.material == "TS10.9"
    assert part.length == Decimal("60")
    assert part.original_qty == Decimal("10")
