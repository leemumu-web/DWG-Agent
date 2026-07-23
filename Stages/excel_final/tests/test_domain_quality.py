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
        theoretical_contribution_unrounded=Decimal("1292.001984"),
    )

    assert web.parent is evidence
    assert web.parent.source.original_qty == Decimal("1")
    assert web.parent.theoretical_unit_weight_unrounded == Decimal("2732.131584")
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
        "来源sheet",
        "来源行",
        "构件编号",
        "零件号",
        "规格",
        "字段",
        "实际值",
        "期望值",
        "绝对误差",
        "相对误差",
        "是否影响part",
        "比重来源",
        "说明",
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
        "数据备注": 12,
        "手册查无": 1,
    }
    assert len(outcome.report_summary["representative_messages"]) == 10
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
