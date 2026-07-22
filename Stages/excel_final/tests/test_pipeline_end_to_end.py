from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook

from handbook import HandbookLookupResult, LookupStatus
from pipeline import run_init_pipeline, run_pipeline


class FakeHandbook:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str | None]] = []

    def lookup(self, category, normalized_spec: str, *, material: str | None = None):
        category_value = getattr(category, "value", str(category))
        self.requests.append((category_value, normalized_spec, material))
        values = {
            ("flat_steel", "6*30"): Decimal("1.413"),
            ("round_bar", "24"): Decimal("3.55"),
            ("rebar", "24"): Decimal("3.55"),
        }
        value = values.get((category_value, normalized_spec))
        status = LookupStatus.HIT if value is not None else LookupStatus.NOT_FOUND
        table = {
            "flat_steel": "flat_steel",
            "round_bar": "round_square_bar",
            "rebar": "rebar",
            "i_beam": "i_beam",
        }.get(category_value, category_value)
        source = f"{table}:{category_value}" if value is not None else f"{table}:not_found"
        return HandbookLookupResult(category_value, normalized_spec, value, source, status)

    def log_stats(self) -> None:
        return None


PARTS = (
    ("p-plate", "PL10*100", "Q355B"),
    ("p-flat", "PL6*30", "Q355B"),
    ("p-bare", "9*91", "Q355B"),
    ("p-box", "BOX100*100*10*10", "Q355B"),
    ("p-round", "D24", "Q355B"),
    ("p-rebar", "D24", "HRB400"),
    ("p-nut", "NUT24", "Q355B"),
    ("p-tt", "TT25", "Q355B"),
    ("p-miss", "I999", "Q355B"),
)


def _tekla_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "原表"
    sheet.append(["测试零件清单"])
    sheet.append([
        "批次", "构件编号", "零件号", "规格", "长度(mm)", "材质", "数量",
        "单净重(kg)", "总净重(kg)", "单毛重(kg)", "总毛重(kg)",
        "单表面积(㎡)", "总表面积(㎡)", "长度(mm)", "宽度(mm)",
        "高度(mm)", "版本",
    ])
    sheet.append(["B1", "C1", None, "BOX100*100*10*10", 1000, "Q355B", 2])
    for part_no, spec, material in PARTS:
        sheet.append([None, None, part_no, spec, 1000, material, 1])
    sheet.append(["B1", "C1", "构件小计", None, None, None, 2])
    workbook.save(path)
    workbook.close()


def _initial_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "初始表"
    sheet.append(["C1材  料  表构件数量：2构件总重：0"])
    sheet.append(["零件号", "截面型材", "长度", "材质", "数量", "单重", "总重", "总面积", "备注"])
    for part_no, spec, material in PARTS:
        sheet.append([part_no, spec, 1000, material, 1, None, None, None, None])
    sheet.append(["合计"])
    workbook.save(path)
    workbook.close()


def _organized(path: Path) -> list[dict[str, object]]:
    workbook = load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = workbook["整理表"]
        headers = [cell.value for cell in sheet[1]]
        return [dict(zip(headers, row, strict=True)) for row in sheet.iter_rows(min_row=2, values_only=True)]
    finally:
        workbook.close()


def _semantic_rows(path: Path) -> list[tuple[object, ...]]:
    return [
        (
            row["零件号"], row["类型"], row["规格"], row["宽度"],
            row["比重"], row["比重来源"], row["导入零件号"],
        )
        for row in _organized(path)
    ]


def test_both_input_adapters_share_the_canonical_engine(tmp_path: Path) -> None:
    tekla = tmp_path / "tekla.xlsx"
    initial = tmp_path / "initial.xlsx"
    _tekla_workbook(tekla)
    _initial_workbook(initial)
    tekla_output = tmp_path / "tekla-output.xlsx"
    initial_output = tmp_path / "initial-output.xlsx"
    tekla_handbook = FakeHandbook()
    initial_handbook = FakeHandbook()

    tekla_outcome = run_pipeline(
        tekla, tekla_output, handbook_repository=tekla_handbook
    )
    initial_outcome = run_init_pipeline(
        initial, initial_output, handbook_repository=initial_handbook
    )

    assert _semantic_rows(tekla_output) == _semantic_rows(initial_output)
    assert tekla_outcome.output_path == tekla_output.resolve()
    assert initial_outcome.output_path == initial_output.resolve()
    assert Path(tekla_outcome) == tekla_output.resolve()
    assert Path(initial_outcome) == initial_output.resolve()
    assert tekla_outcome.quality_status == initial_outcome.quality_status == "warning"
    assert tekla_handbook.requests == initial_handbook.requests
    assert ("round_bar", "24", "Q355B") in tekla_handbook.requests
    assert ("rebar", "24", "HRB400") in tekla_handbook.requests
    assert not any(request[1] in {"NUT24", "TT25"} for request in tekla_handbook.requests)


def test_canonical_pipeline_applies_lookup_split_skip_and_report_rules(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    output = tmp_path / "output.xlsx"
    _tekla_workbook(source)

    outcome = run_pipeline(source, output, handbook_repository=FakeHandbook())
    rows = _organized(output)
    by_part: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_part.setdefault(str(row["零件号"]), []).append(row)

    assert by_part["p-plate"][0]["比重"] == 7.85
    assert by_part["p-flat"][0]["类型"] == "扁钢"
    assert by_part["p-flat"][0]["规格"] == "6*30"
    assert by_part["p-flat"][0]["宽度"] is None
    assert by_part["p-bare"][0]["类型"] == "板材"
    assert by_part["p-bare"][0]["规格"] == 9
    assert by_part["p-bare"][0]["宽度"] == 91
    assert [row["类型"] for row in by_part["p-box"]] == ["BOX腹", "BOX翼"]
    assert by_part["p-box"][0]["序号"] == by_part["p-box"][1]["序号"]
    assert by_part["p-box"][0]["理单重(kg)"] == 28.26
    assert by_part["p-box"][0]["理总重(kg)"] == 56.52
    assert by_part["p-box"][1]["理单重(kg)"] is None
    assert by_part["p-box"][1]["理总重(kg)"] is None
    assert by_part["p-box"][1]["比重"] is None
    assert by_part["p-nut"][0]["比重"] is None
    assert by_part["p-nut"][0]["理单重(kg)"] is None
    assert by_part["p-tt"][0]["比重"] is None
    assert by_part["p-miss"][0]["比重"] == "查无"
    assert outcome.warning_count > 0

    workbook = load_workbook(output, data_only=True, read_only=True)
    try:
        report_rows = list(workbook["处理报告"].iter_rows(min_row=2, values_only=True))
        assert any(row[1] == "五金手册查无" and row[5] == "p-miss" for row in report_rows)
        assert not any(row[1] == "五金手册查无" and row[5] in {"p-nut", "p-tt"} for row in report_rows)
    finally:
        workbook.close()


def test_invalid_confirmed_split_is_reported_without_dropping_source_row(tmp_path: Path) -> None:
    source = tmp_path / "invalid-box.xlsx"
    output = tmp_path / "invalid-box-output.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "原表"
    sheet.append([
        "批次", "构件编号", "零件号", "规格", "长度(mm)", "材质", "数量",
    ])
    sheet.append(["B1", "C1", None, "BOX50*100*10*30", 1000, "Q355B", 1])
    sheet.append([None, None, "bad-box", "BOX50*100*10*30", 1000, "Q355B", 1])
    workbook.save(source)
    workbook.close()

    outcome = run_pipeline(source, output, handbook_repository=FakeHandbook())
    rows = _organized(output)

    assert len(rows) == 1
    assert rows[0]["零件号"] == "bad-box"
    assert rows[0]["类型"] == "BOX"
    assert rows[0]["重量核验"] == "严重"
    assert outcome.quality_status == "severe_warning"
    result = load_workbook(output, read_only=True, data_only=True)
    try:
        assert result["part"].max_row == 1
        report = list(result["处理报告"].iter_rows(min_row=2, values_only=True))
        assert any(row[1] == "拆板几何异常" and row[5] == "bad-box" for row in report)
    finally:
        result.close()
