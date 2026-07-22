from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook


def _contract():
    try:
        return importlib.import_module("input_contract")
    except ModuleNotFoundError as exc:
        pytest.fail(f"input contract module is missing: {exc}")


def _workbook(path: Path, sheet_names: tuple[str, ...]) -> Path:
    workbook = Workbook()
    workbook.active.title = sheet_names[0]
    for name in sheet_names[1:]:
        workbook.create_sheet(name)
    workbook.save(path)
    return path


def test_production_workbook_requires_exactly_one_sheet(tmp_path: Path) -> None:
    contract = _contract()
    source = _workbook(tmp_path / "multi.xlsx", ("原表", "整理", "part"))

    with pytest.raises(contract.InputContractError, match="exactly one worksheet"):
        contract.inspect_production_input(source)


def test_production_single_sheet_and_tekla_text_have_distinct_kinds(tmp_path: Path) -> None:
    contract = _contract()
    workbook_source = _workbook(tmp_path / "single.xlsx", ("原表",))
    text_source = tmp_path / "tekla.xls"
    text_source.write_text("批次\t构件编号\t零件号\t规格\n", encoding="utf-8")

    workbook_input = contract.inspect_production_input(workbook_source)
    text_input = contract.inspect_production_input(text_source)

    assert workbook_input.kind is contract.InputKind.WORKBOOK
    assert workbook_input.sheet_name == "原表"
    assert text_input.kind is contract.InputKind.TEKLA_TEXT
    assert text_input.sheet_name is None


def test_header_detection_resolves_duplicate_length_by_group_semantics(tmp_path: Path) -> None:
    contract = _contract()
    source = tmp_path / "grouped-header.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "原表"
    sheet.append(["零件清单"])
    sheet.append([])
    sheet.append([])
    sheet.append([])
    sheet.append(
        [None, None, None, None, None, None, None, "净重", None, "毛重", None,
         "表面积(㎡)", None, "构件形状尺寸", None, None, None]
    )
    sheet.append(
        [
            "批次", "构件编号", "零件号", "规格", "长度(mm)", "材质", "数量",
            "单净重(kg)", "总净重(kg)", "单毛重(kg)", "总毛重(kg)",
            "单表面积(㎡)", "总表面积(㎡)", "长度(mm)", "宽度(mm)",
            "高度(mm)", "版本",
        ]
    )
    workbook.save(source)
    workbook.close()
    loaded = load_workbook(source, read_only=True, data_only=False)
    try:
        detection = contract.detect_canonical_header(loaded["原表"])
    finally:
        loaded.close()

    assert detection.row_number == 6
    assert detection.columns == {
        "批次": 1,
        "构件编号": 2,
        "零件号": 3,
        "规格": 4,
        "零件长度": 5,
        "材质": 6,
        "数量": 7,
        "单净重": 8,
        "总净重": 9,
        "单毛重": 10,
        "总毛重": 11,
        "单表面积": 12,
        "总表面积": 13,
        "构件长度": 14,
        "构件宽度": 15,
        "构件高度": 16,
        "版本": 17,
    }


def test_header_detection_rejects_equally_complete_rows_with_diagnostics(tmp_path: Path) -> None:
    contract = _contract()
    source = tmp_path / "ambiguous-header.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    header = ["批次", "构件编号", "零件号", "规格", "长度(mm)", "材质", "数量"]
    sheet.append(["零件清单"])
    sheet.append(header)
    sheet.append(["说明"])
    sheet.append(header)
    workbook.save(source)
    workbook.close()

    loaded = load_workbook(source, read_only=True, data_only=False)
    try:
        with pytest.raises(contract.InputContractError) as caught:
            contract.detect_canonical_header(loaded.active)
    finally:
        loaded.close()

    message = str(caught.value)
    assert "ambiguous canonical header" in message
    assert "first 15 candidate scores" in message
    assert "row=2" in message
    assert "row=4" in message
    assert "missing=[]" in message


def test_header_detection_rejects_incomplete_row_six_without_fallback(tmp_path: Path) -> None:
    contract = _contract()
    source = tmp_path / "incomplete-row-six.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    for _ in range(5):
        sheet.append([None])
    sheet.append(["批次", "构件编号", "零件号", "规格", "长度(mm)", "材质"])
    workbook.save(source)
    workbook.close()

    loaded = load_workbook(source, read_only=True, data_only=False)
    try:
        with pytest.raises(contract.InputContractError) as caught:
            contract.detect_canonical_header(loaded.active)
    finally:
        loaded.close()

    message = str(caught.value)
    assert "missing required fields" in message
    assert "数量" in message
    assert "row=6" in message
    assert "first 15 candidate scores" in message
