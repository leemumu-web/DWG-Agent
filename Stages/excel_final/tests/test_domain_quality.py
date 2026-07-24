from __future__ import annotations

import importlib
import os
from dataclasses import FrozenInstanceError
from decimal import Decimal
from pathlib import Path

import pytest


def _domain():
    try:
        return importlib.import_module("domain")
    except ModuleNotFoundError as exc:
        pytest.fail(f"canonical domain module is missing: {exc}")


def _quality():
    try:
        return importlib.import_module("quality")
    except ModuleNotFoundError as exc:
        pytest.fail(f"quality ledger module is missing: {exc}")


def test_source_part_is_immutable_and_preserves_source_evidence() -> None:
    domain = _domain()

    part = domain.SourcePart(
        source_sheet="原表",
        source_row=9,
        source_seq="7",
        batch="B7地下",
        component_no="B7-CB-71",
        component_qty=Decimal("2"),
        part_no="P-17",
        original_spec="PL10*135",
        material="Q355B",
        length=Decimal("250"),
        original_qty=Decimal("3"),
        source_unit_net=Decimal("2.613"),
        source_total_net=Decimal("7.839"),
        source_unit_gross=Decimal("2.649"),
        source_total_gross=Decimal("7.947"),
        source_unit_area=Decimal("0.075"),
        source_total_area=Decimal("0.225"),
        classification=None,
    )

    assert part.source_sheet == "原表"
    assert part.source_row == 9
    assert part.source_seq == "7"
    assert part.component_no == "B7-CB-71"
    assert part.original_spec == "PL10*135"
    assert part.original_qty == Decimal("3")
    assert part.source_unit_net == Decimal("2.613")
    assert part.source_unit_gross == Decimal("2.649")
    with pytest.raises(FrozenInstanceError):
        part.part_no = "changed"


def test_parent_evidence_and_split_part_share_one_weight_source() -> None:
    domain = _domain()
    source = domain.SourcePart(
        source_sheet="原表",
        source_row=77,
        source_seq=31,
        batch="B7地下",
        component_no="B7-BOX-01",
        component_qty=Decimal("2"),
        part_no="BOX-P1",
        original_spec="BOX700*700*36*36",
        material="Q355B",
        length=Decimal("3640"),
        original_qty=Decimal("1"),
        source_unit_net=Decimal("2732.132"),
        source_total_net=Decimal("2732.132"),
        source_unit_gross=Decimal("2732.132"),
        source_total_gross=Decimal("2732.132"),
        source_unit_area=None,
        source_total_area=None,
        classification="BOX",
    )
    evidence = domain.ParentPartEvidence(
        source=source,
        normalized_type="BOX",
        normalized_spec="BOX700*700*36*36",
        normalized_width=None,
        density_value=Decimal("7.85"),
        density_source="板材常量:7.85",
        theoretical_unit_weight_unrounded=Decimal("2732.131584"),
        theoretical_total_weight_unrounded=Decimal("5464.263168"),
        material_utilization=Decimal("1.0000001523"),
        weight_validation_status="ok",
        weight_validation_details=("理论单重与源单毛重一致",),
    )
    web = domain.SplitPart(
        parent=evidence,
        part_type="BOX腹",
        import_component_no="B7-BOX-01",
        import_part_no="BOX-P1-BOX腹",
        spec=Decimal("36"),
        width=Decimal("628"),
        quantity=Decimal("2"),
        is_main=True,
        theoretical_unit_weight_unrounded=Decimal("646.000992"),
        theoretical_contribution_unrounded=Decimal("1292.001984"),
    )

    assert web.parent is evidence
    assert web.parent.source.original_qty == Decimal("1")
    assert web.parent.theoretical_unit_weight_unrounded == Decimal("2732.131584")
    assert web.theoretical_unit_weight_unrounded == Decimal("646.000992")
    assert web.theoretical_contribution_unrounded == Decimal("1292.001984")
    with pytest.raises(FrozenInstanceError):
        evidence.density_source = "changed"
    with pytest.raises(FrozenInstanceError):
        web.quantity = Decimal("4")


def test_quality_ledger_counts_issue_details_and_serializes_report_rows() -> None:
    quality = _quality()
    ledger = quality.QualityLedger()
    ledger.add(
        quality.QualityIssue(
            level=quality.IssueLevel.WARNING,
            category="手册查无",
            source_sheet="原表",
            source_row=20,
            component_no="C-1",
            part_no="P-1",
            spec="L999",
            field="比重",
            actual_value="查无",
            expected_value="角钢表命中",
            absolute_error=None,
            relative_error=None,
            affects_part=False,
            density_source="角钢:not_found",
            description="在线查询成功但无记录",
        )
    )
    ledger.add(
        quality.QualityIssue(
            level=quality.IssueLevel.WARNING,
            category="源重量缺失",
            source_sheet="原表",
            source_row=20,
            component_no="C-1",
            part_no="P-1",
            spec="L999",
            field="单毛重",
            actual_value=None,
            expected_value="可核验的源单毛重",
            absolute_error=None,
            relative_error=None,
            affects_part=False,
            density_source="角钢:not_found",
            description="无法完成理论单重核验",
        )
    )
    ledger.add(
        quality.QualityIssue(
            level=quality.IssueLevel.SEVERE,
            category="重量物理异常",
            source_sheet="原表",
            source_row=21,
            component_no="C-1",
            part_no="P-2",
            spec="PL10*100",
            field="单净重",
            actual_value="10.500",
            expected_value="<= 10.000",
            absolute_error=Decimal("0.500"),
            relative_error=Decimal("0.05"),
            affects_part=True,
            density_source="板材常量:7.85",
            description="单净重显著超过理论毛坯上界",
        )
    )

    assert ledger.warning_count == 2
    assert ledger.severe_warning_count == 1
    assert ledger.quality_status == quality.QualityStatus.SEVERE_WARNING
    report_rows = ledger.report_rows()
    assert len(report_rows) == 3
    assert tuple(report_rows[0]) == (
        "级别",
        "类别",
        "来源位置",
        "构件编号",
        "零件号",
        "涉及字段",
        "说明",
        "建议操作",
    )
    assert all(row["建议操作"] for row in report_rows)


def test_quality_report_filters_info_and_merges_same_source_category() -> None:
    quality = _quality()
    ledger = quality.QualityLedger()
    for field in ("长度", "材质"):
        ledger.add(
            quality.QualityIssue(
                level=quality.IssueLevel.SEVERE,
                category="关键字段缺失",
                source_sheet="原表",
                source_row=8,
                component_no="C1",
                part_no="P1",
                spec="PL10*100",
                field=field,
                actual_value=None,
                expected_value="非空源值",
                absolute_error=None,
                relative_error=None,
                affects_part=True,
                density_source=None,
                description=f"{field}缺失",
            )
        )
    ledger.add(
        quality.QualityIssue(
            level=quality.IssueLevel.INFO,
            category="数据备注",
            source_sheet="原表",
            source_row=8,
            component_no="C1",
            part_no="P1",
            spec="PL10*100",
            field="备注",
            actual_value=None,
            expected_value=None,
            absolute_error=None,
            relative_error=None,
            affects_part=False,
            density_source=None,
            description="无需人工处理",
        )
    )

    assert ledger.report_rows() == [
        {
            "级别": "严重",
            "类别": "关键字段缺失",
            "来源位置": "原表!8",
            "构件编号": "C1",
            "零件号": "P1",
            "涉及字段": "长度；材质",
            "说明": "长度缺失；材质缺失",
            "建议操作": "补齐涉及字段后重新处理",
        }
    ]
    outcome = ledger.to_outcome(Path("result.xlsx"))
    assert outcome.report_summary == {
        "info_count": 0,
        "warning_count": 0,
        "severe_warning_count": 1,
        "category_counts": {"关键字段缺失": 1},
        "representative_messages": ["长度缺失；材质缺失"],
    }


def test_quality_report_groups_repeated_action_across_source_rows() -> None:
    quality = _quality()
    ledger = quality.QualityLedger()
    for source_row in (10, 11):
        ledger.add(
            quality.QualityIssue(
                level=quality.IssueLevel.WARNING,
                category="五金手册查无",
                source_sheet="原表",
                source_row=source_row,
                component_no=f"C{source_row}",
                part_no=f"P{source_row}",
                spec="D8",
                field="比重",
                actual_value="查无",
                expected_value="指定类别手册命中",
                absolute_error=None,
                relative_error=None,
                affects_part=False,
                density_source="unsupported:not_found",
                description="D8: D系列材质不足",
            )
        )
    ledger.add(
        quality.QualityIssue(
            level=quality.IssueLevel.WARNING,
            category="五金手册查无",
            source_sheet="原表",
            source_row=12,
            component_no="C12",
            part_no="P12",
            spec="D12",
            field="比重",
            actual_value="查无",
            expected_value="指定类别手册命中",
            absolute_error=None,
            relative_error=None,
            affects_part=False,
            density_source="unsupported:not_found",
            description="D12: D系列材质不足",
        )
    )

    rows = ledger.report_rows()

    assert len(rows) == 2
    d8 = next(row for row in rows if "D8" in str(row["说明"]))
    assert d8["来源位置"] == "原表!10、11"
    assert d8["构件编号"] == "C10、C11"
    assert d8["零件号"] == "P10、P11"
    assert d8["涉及字段"] == "比重"
    assert d8["说明"] == "影响 2 行；D8: D系列材质不足"
    assert ledger.warning_count == 2


def test_quality_report_compacts_geometry_review_and_prioritizes_severe_rows() -> None:
    quality = _quality()
    ledger = quality.QualityLedger()
    for source_row, spec, actual, expected in (
        (10, "BH500*300*10*16", Decimal("120"), Decimal("100")),
        (11, "BOX400*400*16*16", Decimal("90"), Decimal("100")),
    ):
        ledger.add(
            quality.QualityIssue(
                level=quality.IssueLevel.WARNING,
                category="几何理论重与毛重",
                source_sheet="原表",
                source_row=source_row,
                component_no=f"C{source_row}",
                part_no=f"P{source_row}",
                spec=spec,
                field="单毛重",
                actual_value=actual,
                expected_value=expected,
                absolute_error=abs(actual - expected),
                relative_error=abs(actual - expected) / expected,
                affects_part=False,
                density_source="plate_constant:7.85",
                description="单毛重与父理论重量偏差超限",
            )
        )
    ledger.add(
        quality.QualityIssue(
            level=quality.IssueLevel.SEVERE,
            category="源重量链异常",
            source_sheet="原表",
            source_row=10,
            component_no="C10",
            part_no="P10",
            spec="BH500*300*10*16",
            field="总毛重",
            actual_value=Decimal("250"),
            expected_value=Decimal("240"),
            absolute_error=Decimal("10"),
            relative_error=Decimal("0.0416667"),
            affects_part=True,
            density_source="plate_constant:7.85",
            description="总毛重不等于对应单重乘原数量",
        )
    )

    rows = ledger.report_rows()

    assert len(rows) == 2
    geometry = next(row for row in rows if row["类别"] == "几何理论重与毛重")
    assert geometry["来源位置"] == "原表!11"
    assert geometry["说明"] == (
        "源毛重低于BOX拆板合计父理论重（腹板×2+翼板×2）；"
        "最大相对偏差 10.00%"
    )
    assert geometry["建议操作"] == ("抽查轮廓、切割和毛坯口径；仅在源毛重用于下料或采购时人工确认")
    severe = next(row for row in rows if row["类别"] == "源重量链异常")
    assert severe["来源位置"] == "原表!10"


@pytest.mark.parametrize(
    ("spec", "expected_basis"),
    [
        ("BH500*300*10*16", "BH拆板合计父理论重（腹板×1+翼板×2）"),
        ("BOX400*400*16*16", "BOX拆板合计父理论重（腹板×2+翼板×2）"),
        ("BT500*300*10*16", "BT拆板合计父理论重（腹板×1+翼板×1）"),
    ],
)
def test_geometry_report_names_weighted_fabricated_parent_basis(
    spec: str,
    expected_basis: str,
) -> None:
    quality = _quality()
    ledger = quality.QualityLedger()
    ledger.add(
        quality.QualityIssue(
            level=quality.IssueLevel.WARNING,
            category="几何理论重与毛重",
            source_sheet="原表",
            source_row=8,
            component_no="C1",
            part_no="P1",
            spec=spec,
            field="单毛重",
            actual_value=Decimal("90"),
            expected_value=Decimal("100"),
            absolute_error=Decimal("10"),
            relative_error=Decimal("0.1"),
            affects_part=False,
            density_source="plate_constant:7.85",
            description="单毛重与父理论重量偏差超限",
        )
    )

    report = ledger.report_rows()

    assert len(report) == 1
    assert report[0]["说明"] == (
        f"源毛重低于{expected_basis}；最大相对偏差 10.00%"
    )


def test_quality_report_keeps_handbook_specs_separate_for_standard_review() -> None:
    quality = _quality()
    ledger = quality.QualityLedger()
    for source_row, spec in ((20, "HN400*200*8*13"), (21, "HN450*200*9*14")):
        ledger.add(
            quality.QualityIssue(
                level=quality.IssueLevel.WARNING,
                category="手册理论重与毛重",
                source_sheet="原表",
                source_row=source_row,
                component_no=f"C{source_row}",
                part_no=f"P{source_row}",
                spec=spec,
                field="单毛重",
                actual_value=Decimal("66"),
                expected_value=Decimal("65.4"),
                absolute_error=Decimal("0.6"),
                relative_error=Decimal("0.009174"),
                affects_part=False,
                density_source="h_beam:h_beam",
                description="单毛重与父理论重量偏差超限",
            )
        )

    rows = ledger.report_rows()

    assert len(rows) == 2
    assert all(
        row["建议操作"] == "确认项目采用的型材标准版本；需要时补充版本映射后重新处理"
        for row in rows
    )


@pytest.mark.parametrize(
    ("level_name", "affects_part"),
    [
        ("SEVERE", False),
        ("FATAL", False),
        ("WARNING", True),
        ("INFO", True),
    ],
)
def test_quality_issue_level_controls_part_isolation(
    level_name: str,
    affects_part: bool,
) -> None:
    quality = _quality()

    with pytest.raises(ValueError, match="affects_part"):
        quality.QualityIssue(
            level=getattr(quality.IssueLevel, level_name),
            category="级别约束",
            source_sheet="原表",
            source_row=1,
            component_no=None,
            part_no=None,
            spec=None,
            field=None,
            actual_value=None,
            expected_value=None,
            absolute_error=None,
            relative_error=None,
            affects_part=affects_part,
            density_source=None,
            description="非法级别/隔离组合",
        )


def test_quality_ledger_builds_path_compatible_bounded_outcome(tmp_path: Path) -> None:
    quality = _quality()
    ledger = quality.QualityLedger()
    for index in range(12):
        ledger.add(
            quality.QualityIssue(
                level=quality.IssueLevel.INFO,
                category="数据备注",
                source_sheet="原表",
                source_row=index + 2,
                component_no="C-1",
                part_no=f"P-{index}",
                spec="PL10*100",
                field="备注",
                actual_value=None,
                expected_value="已复核",
                absolute_error=None,
                relative_error=None,
                affects_part=False,
                density_source="板材常量:7.85",
                description=f"第 {index + 1} 条数据备注",
            )
        )
    ledger.add(
        quality.QualityIssue(
            level=quality.IssueLevel.WARNING,
            category="手册查无",
            source_sheet="原表",
            source_row=30,
            component_no="C-2",
            part_no="P-X",
            spec="L999",
            field="比重",
            actual_value="查无",
            expected_value="角钢表命中",
            absolute_error=None,
            relative_error=None,
            affects_part=False,
            density_source="角钢:not_found",
            description="角钢表未命中",
        )
    )
    output_path = tmp_path / "result.xlsx"

    outcome = ledger.to_outcome(output_path)

    assert os.fspath(outcome) == str(output_path.resolve())
    assert outcome.output_path == output_path.resolve()
    assert outcome.quality_status == "warning"
    assert outcome.warning_count == 1
    assert outcome.severe_warning_count == 0
    assert outcome.report_summary["category_counts"] == {
        "手册查无": 1,
    }
    assert outcome.report_summary["info_count"] == 0
    assert outcome.report_summary["representative_messages"] == ["角钢表未命中"]
    with pytest.raises(FrozenInstanceError):
        outcome.warning_count = 99


@pytest.mark.parametrize("include_info", [False, True])
def test_quality_status_is_ok_without_warning_or_severe_issue(include_info: bool) -> None:
    quality = _quality()
    ledger = quality.QualityLedger()
    if include_info:
        ledger.add(
            quality.QualityIssue(
                level=quality.IssueLevel.INFO,
                category="低利用率",
                source_sheet="原表",
                source_row=5,
                component_no="C-1",
                part_no="P-1",
                spec="PL10*100",
                field="净材利用率",
                actual_value="37.67%",
                expected_value=None,
                absolute_error=None,
                relative_error=None,
                affects_part=False,
                density_source="板材常量:7.85",
                description="只记录，不设置下限",
            )
        )

    assert ledger.quality_status == quality.QualityStatus.OK
