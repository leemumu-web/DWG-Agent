from __future__ import annotations

import importlib
import logging
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from domain import ComponentRowKind, ComponentSourceRow, SourcePart
from part_builder import PartRow
from quality import IssueLevel, QualityIssue


def _writer():
    return importlib.import_module("writer_parts")


def _source(path: Path) -> tuple[SourcePart, tuple[ComponentSourceRow, ...]]:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "原表"
    sheet["A1"] = "原始标题"
    sheet["A1"].font = Font(bold=True, color="123456")
    sheet["B2"] = " 保 留 空 格 "
    workbook.save(path)
    workbook.close()

    part = SourcePart(
        source_sheet="原表",
        source_row=8,
        source_seq=7,
        batch="B1",
        component_no="C1",
        component_qty=Decimal("2"),
        part_no="p1",
        original_spec="PL10*100",
        material="Q355B",
        length=Decimal("1000"),
        original_qty=Decimal("3"),
        source_unit_net=Decimal("7.8"),
        source_total_net=Decimal("23.4"),
        source_unit_gross=Decimal("7.85"),
        source_total_gross=Decimal("23.55"),
        source_unit_area=Decimal("0.22"),
        source_total_area=Decimal("0.66"),
        classification="板材",
    )
    rows = (
        ComponentSourceRow(
            source_sheet="原表",
            source_row=7,
            kind=ComponentRowKind.SUMMARY,
            batch="B1",
            component_no="C1",
            component_qty=Decimal("2"),
            original_spec="BOX700*700*36*36",
            material="Q355B",
            source_unit_net=Decimal("7.8"),
            source_total_net=Decimal("15.6"),
            source_unit_gross=Decimal("7.85"),
            source_total_gross=Decimal("15.7"),
            source_unit_area=Decimal("0.22"),
            source_total_area=Decimal("0.44"),
            component_length=Decimal("1000"),
            component_width=Decimal("100"),
            component_height=Decimal("10"),
            subtotal_source_row=9,
        ),
    )
    return part, rows


def _organized_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "序号": 7,
        "构件编号": "C1",
        "导入构件编号": "C1",
        "构件数": Decimal("2"),
        "类型": "板材",
        "班组": "",
        "批次": "B1",
        "零件号": "p1",
        "导入零件号": "p1",
        "截面型材": "PL10*100",
        "规格": Decimal("10"),
        "宽度": Decimal("100"),
        "长度(mm)": Decimal("1000"),
        "左进(mm)": None,
        "右进(mm)": None,
        "下料长度(mm)": Decimal("1000"),
        "材质": "Q355B",
        "原数量": Decimal("3"),
        "数量": Decimal("3"),
        "总数": Decimal("6"),
        "总长(mm)": Decimal("6000"),
        "比重": "查无",
        "比重来源": "flat_steel:not_found",
        "理单重(kg)": Decimal("7.85"),
        "理总重(kg)": Decimal("47.10"),
        "单净重(kg)": Decimal("7.8"),
        "总净重(kg)": Decimal("23.4"),
        "表净重(kg)": Decimal("46.8"),
        "单毛重(kg)": Decimal("7.85"),
        "总毛重(kg)": Decimal("23.55"),
        "表毛重(kg)": Decimal("47.10"),
        "净材利用率": Decimal("0.993630573"),
        "重量核验": "严重",
        "单表面积(㎡)": Decimal("0.22"),
        "总表面积(㎡)": Decimal("0.66"),
        "_source_row": 8,
    }
    row.update(overrides)
    return row


def _issues() -> tuple[QualityIssue, ...]:
    common = {
        "source_sheet": "原表",
        "source_row": 8,
        "component_no": "C1",
        "part_no": "p1",
        "spec": "PL10*100",
        "absolute_error": None,
        "relative_error": None,
        "density_source": "flat_steel:not_found",
    }
    return (
        QualityIssue(
            level=IssueLevel.WARNING,
            category="五金手册查无",
            field="比重",
            actual_value="查无",
            expected_value="手册命中",
            affects_part=False,
            description="扁钢规格查无",
            **common,
        ),
        QualityIssue(
            level=IssueLevel.SEVERE,
            category="几何理论重与毛重",
            field="单毛重",
            actual_value=Decimal("8.5"),
            expected_value=Decimal("7.85"),
            affects_part=True,
            description="单毛重偏差严重",
            **common,
        ),
    )


def test_canonical_writer_emits_fixed_six_sheets_and_audited_styles(tmp_path: Path) -> None:
    writer = _writer()
    source = tmp_path / "source.xlsx"
    part, component_rows = _source(source)
    output = tmp_path / "output.xlsx"
    organized = [_organized_row(), _organized_row(类型="BOX翼", 序号=7, 导入零件号="p1-BOX翼")]
    part_rows = (
        PartRow("C1", "p1", Decimal("10"), Decimal("100"), Decimal("1000"),
                "Q355B", Decimal("6"), "", "", "板材", "RECT"),
    )

    outcome = writer.write_canonical_workbook(
        source,
        output,
        cleaned_parts=(part,),
        component_rows=component_rows,
        organized_rows=organized,
        part_rows=part_rows,
        issues=_issues(),
    )

    workbook = load_workbook(output, data_only=False)
    try:
        assert workbook.sheetnames == ["原表", "清洗表", "构件表", "整理表", "part", "处理报告"]
        assert workbook["原表"]["A1"].value == "原始标题"
        assert workbook["原表"]["A1"].font.bold is True
        assert workbook["原表"]["B2"].value == " 保 留 空 格 "
        assert [cell.value for cell in workbook["整理表"][1]] == writer.ORGANIZED_HEADERS
        assert [cell.value for cell in workbook["part"][1]] == writer.PART_HEADERS
        assert [cell.value for cell in workbook["处理报告"][1]] == writer.REPORT_HEADERS
        assert workbook["整理表"]["A2"].value == workbook["整理表"]["A3"].value == 7
        assert workbook["整理表"]["P2"].value == "=M2-N2-O2"
        assert workbook["整理表"]["X2"].number_format == "0.000"
        assert workbook["整理表"]["V2"].font.color.rgb.endswith("FF0000")
        assert workbook["整理表"]["AC2"].fill.fill_type == "solid"
        assert workbook["整理表"]["AG2"].fill.fill_type == "solid"
        assert workbook["part"].max_row == 2
        assert workbook["part"]["H2"].value is None
        assert workbook["part"]["I2"].value is None
        assert workbook["处理报告"].max_row == 3
        assert workbook["构件表"].max_row == 2
        assert workbook["构件表"]["C2"].value == "summary"
        assert workbook["构件表"]["D2"].value == 9
    finally:
        workbook.close()

    assert outcome.output_path == output.resolve()
    assert outcome.warning_count == 1
    assert outcome.severe_warning_count == 1
    assert outcome.quality_status == "severe_warning"


def test_writer_formula_cache_is_immediately_readable_data_only(tmp_path: Path) -> None:
    writer = _writer()
    source = tmp_path / "source.xlsx"
    part, component_rows = _source(source)
    output = tmp_path / "output.xlsx"

    writer.write_canonical_workbook(
        source,
        output,
        cleaned_parts=(part,),
        component_rows=component_rows,
        organized_rows=[_organized_row(**{"左进(mm)": Decimal("10"), "右进(mm)": Decimal("5"),
                                          "下料长度(mm)": Decimal("985")})],
        part_rows=(),
        issues=(),
    )

    formulas = load_workbook(output, data_only=False, read_only=True)
    values = load_workbook(output, data_only=True, read_only=True)
    try:
        assert formulas["整理表"]["P2"].value == "=M2-N2-O2"
        assert values["整理表"]["P2"].value == 985
    finally:
        formulas.close()
        values.close()


def test_canonical_writer_rejects_non_xlsx_output(tmp_path: Path) -> None:
    writer = _writer()
    source = tmp_path / "source.xlsx"
    part, component_rows = _source(source)
    output = tmp_path / "output.xlsm"

    with pytest.raises(ValueError, match=r"\.xlsx"):
        writer.write_canonical_workbook(
            source,
            output,
            cleaned_parts=(part,),
            component_rows=component_rows,
            organized_rows=[_organized_row()],
            part_rows=(),
            issues=(),
        )

    assert not output.exists()


def test_canonical_writer_log_does_not_disclose_absolute_path(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writer = _writer()
    source = tmp_path / "private-source.xlsx"
    part, component_rows = _source(source)
    output = tmp_path / "private-output.xlsx"

    with caplog.at_level(logging.INFO, logger="writer_parts"):
        writer.write_canonical_workbook(
            source,
            output,
            cleaned_parts=(part,),
            component_rows=component_rows,
            organized_rows=[_organized_row()],
            part_rows=(),
            issues=(),
        )

    assert output.name in caplog.text
    assert str(tmp_path) not in caplog.text
