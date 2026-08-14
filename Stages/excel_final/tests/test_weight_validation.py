from __future__ import annotations

import importlib
from decimal import Decimal

import pytest

from domain import SourcePart


def _weights():
    try:
        return importlib.import_module("weights")
    except ModuleNotFoundError as exc:
        pytest.fail(f"weights module is missing: {exc}")


def _source(**overrides: object) -> SourcePart:
    values: dict[str, object] = {
        "source_sheet": "原表",
        "source_row": 8,
        "source_seq": 1,
        "batch": "B1",
        "component_no": "C1",
        "component_qty": Decimal("2"),
        "part_no": "p1",
        "original_spec": "PL10*100",
        "material": "Q355B",
        "length": Decimal("1000"),
        "original_qty": Decimal("3"),
        "source_unit_net": Decimal("8"),
        "source_total_net": Decimal("24"),
        "source_unit_gross": Decimal("10"),
        "source_total_gross": Decimal("30"),
        "source_unit_area": Decimal("0.2"),
        "source_total_area": Decimal("0.6"),
        "classification": "板材",
    }
    values.update(overrides)
    return SourcePart(**values)


def test_source_multiplication_chain_allows_exactly_point_one_kg() -> None:
    weights = _weights()
    source = _source(
        source_total_net=Decimal("24.1"),
        source_total_gross=Decimal("30.1"),
    )

    result = weights.validate_parent_weights(
        source,
        normalized_type="板材",
        normalized_spec="10",
        normalized_width=Decimal("100"),
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight=Decimal("10"),
        theory_basis=weights.TheoryBasis.GEOMETRY,
    )

    assert not [issue for issue in result.issues if issue.category == "源重量链异常"]


def test_source_multiplication_chain_above_point_one_is_severe_and_exact() -> None:
    weights = _weights()
    source = _source(source_total_gross=Decimal("30.1001"))

    result = weights.validate_parent_weights(
        source,
        normalized_type="板材",
        normalized_spec="10",
        normalized_width=Decimal("100"),
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight=Decimal("10"),
        theory_basis=weights.TheoryBasis.GEOMETRY,
    )

    issues = [issue for issue in result.issues if issue.category == "源重量链异常"]
    assert len(issues) == 1
    assert issues[0].field == "总毛重"
    assert issues[0].expected_value == Decimal("30")
    assert issues[0].actual_value == Decimal("30.1001")
    assert issues[0].affects_part is True


@pytest.mark.parametrize(
    ("gross", "expected"),
    [
        ("100.01", "pass"),
        ("100.5", "pass"),
        ("100.5001", "warning"),
        ("102", "warning"),
        ("102.0001", "severe"),
    ],
)
def test_theory_to_gross_threshold_boundaries(gross: str, expected: str) -> None:
    # 参数化边界值刻意贴边构造，对应 weights.py 的判定阈值：
    # 100.01 = 绝对容差 0.01 内 → pass；100.5 = 恰好 0.5% → pass；
    # 100.5001 越过 0.5% → warning；102 = 恰好 2% → warning；
    # 102.0001 越过 2% → severe。改容差必须先改这里。
    weights = _weights()

    assessment = weights.assess_theory_against_gross(
        Decimal("100"),
        Decimal(gross),
    )

    assert assessment.level.value == expected


def test_low_material_utilization_is_recorded_without_a_low_cutoff_issue() -> None:
    weights = _weights()
    source = _source(
        source_unit_net=Decimal("3.767"),
        source_total_net=Decimal("11.301"),
    )

    result = weights.validate_parent_weights(
        source,
        normalized_type="板材",
        normalized_spec="10",
        normalized_width=Decimal("100"),
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight=Decimal("10"),
        theory_basis=weights.TheoryBasis.GEOMETRY,
    )

    assert result.evidence.material_utilization == Decimal("0.3767")
    assert not [issue for issue in result.issues if issue.category == "净材利用率过低"]


@pytest.mark.parametrize(
    ("net", "expected"),
    [
        ("100.5", "pass"),
        ("100.5001", "warning"),
        ("102", "warning"),
        ("102.0001", "severe"),
    ],
)
def test_net_above_theory_threshold_boundaries(net: str, expected: str) -> None:
    weights = _weights()

    assessment = weights.assess_net_against_theory(Decimal(net), Decimal("100"))

    assert assessment.level.value == expected


def test_tiny_net_excess_within_absolute_rounding_tolerance_passes() -> None:
    weights = _weights()

    assessment = weights.assess_net_against_theory(
        Decimal("0.081"),
        Decimal("0.080541"),
    )

    assert assessment.level.value == "pass"


def test_large_geometry_to_gross_deviation_warns_without_isolating_part() -> None:
    weights = _weights()
    source = _source(
        component_qty=Decimal("1"),
        original_qty=Decimal("1"),
        source_unit_net=Decimal("90"),
        source_total_net=Decimal("90"),
        source_unit_gross=Decimal("103"),
        source_total_gross=Decimal("103"),
    )

    result = weights.validate_parent_weights(
        source,
        normalized_type="BH",
        normalized_spec="BH500*300*10*16",
        normalized_width=None,
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight=Decimal("100"),
        theory_basis=weights.TheoryBasis.GEOMETRY,
    )

    issues = [issue for issue in result.issues if issue.category == "几何理论重与毛重"]
    assert issues
    assert all(issue.level.value == "警告" for issue in issues)
    assert all(issue.affects_part is False for issue in issues)
    assert result.evidence.weight_validation_status == "warning"


def test_large_handbook_to_gross_deviation_remains_severe() -> None:
    weights = _weights()
    source = _source(
        component_qty=Decimal("1"),
        original_qty=Decimal("1"),
        source_unit_net=Decimal("90"),
        source_total_net=Decimal("90"),
        source_unit_gross=Decimal("103"),
        source_total_gross=Decimal("103"),
    )

    result = weights.validate_parent_weights(
        source,
        normalized_type="H型钢",
        normalized_spec="H200*200*8*12",
        normalized_width=None,
        density_value=Decimal("49.9"),
        density_source="h_beam:h_beam",
        theoretical_unit_weight=Decimal("100"),
        theory_basis=weights.TheoryBasis.HANDBOOK,
    )

    issues = [issue for issue in result.issues if issue.category == "手册理论重与毛重"]
    assert issues
    assert all(issue.level.value == "严重" for issue in issues)
    assert all(issue.affects_part is True for issue in issues)
    assert result.evidence.weight_validation_status == "severe_warning"


def test_theory_total_absolute_tolerance_scales_with_source_quantity() -> None:
    weights = _weights()
    source = _source(
        component_qty=Decimal("1"),
        original_qty=Decimal("3"),
        source_unit_net=Decimal("0.47"),
        source_total_net=Decimal("1.40"),
        source_unit_gross=Decimal("0.475"),
        source_total_gross=Decimal("1.415"),
    )

    result = weights.validate_parent_weights(
        source,
        normalized_type="角钢",
        normalized_spec="L50*5",
        normalized_width=None,
        density_value=Decimal("3.77"),
        density_source="angle:angle",
        theoretical_unit_weight=Decimal("0.47502"),
        theory_basis=weights.TheoryBasis.HANDBOOK,
    )

    assert not [issue for issue in result.issues if issue.category == "手册理论重与毛重"]


@pytest.mark.parametrize("basis_name", ["GEOMETRY", "HANDBOOK"])
def test_net_above_theory_is_review_only_for_each_theory_basis(
    basis_name: str,
) -> None:
    weights = _weights()
    source = _source(
        component_qty=Decimal("1"),
        original_qty=Decimal("1"),
        source_unit_net=Decimal("103"),
        source_total_net=Decimal("103"),
        source_unit_gross=Decimal("103"),
        source_total_gross=Decimal("103"),
    )

    result = weights.validate_parent_weights(
        source,
        normalized_type="板材",
        normalized_spec="10",
        normalized_width=Decimal("100"),
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight=Decimal("100"),
        theory_basis=getattr(weights.TheoryBasis, basis_name),
    )

    issues = [issue for issue in result.issues if issue.category == "净重大于理论重"]
    assert len(issues) == 1
    assert issues[0].level.value == "警告"
    assert issues[0].affects_part is False


def test_missing_source_weights_warn_without_backfill_or_isolation() -> None:
    weights = _weights()
    source = _source(
        source_unit_net=None,
        source_total_net=None,
        source_unit_gross=None,
        source_total_gross=None,
    )

    result = weights.validate_parent_weights(
        source,
        normalized_type="板材",
        normalized_spec="10",
        normalized_width=Decimal("100"),
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight=Decimal("7.85"),
        theory_basis=weights.TheoryBasis.GEOMETRY,
    )

    missing = [issue for issue in result.issues if issue.category == "源重量缺失"]
    assert {issue.field for issue in missing} == {"单净重", "总净重", "单毛重", "总毛重"}
    assert all(issue.affects_part is False for issue in missing)
    assert result.evidence.theoretical_unit_weight_unrounded == Decimal("7.85")
    assert result.evidence.theoretical_total_weight_unrounded == Decimal("47.10")
    assert source.source_unit_gross is None
    assert result.evidence.weight_validation_status == "warning"


def test_explicit_skip_keeps_theory_blank_but_still_validates_source_chain() -> None:
    weights = _weights()
    source = _source(original_spec="NUT_M24", classification="螺母")

    result = weights.validate_parent_weights(
        source,
        normalized_type="螺母",
        normalized_spec="NUT_M24",
        normalized_width=None,
        density_value=None,
        density_source="explicit_skip",
        theoretical_unit_weight=None,
        theory_basis=weights.TheoryBasis.HANDBOOK,
    )

    assert result.evidence.theoretical_unit_weight_unrounded is None
    assert result.evidence.theoretical_total_weight_unrounded is None
    assert result.evidence.material_utilization is None
    assert not [issue for issue in result.issues if "理论重" in issue.category]


def test_severe_physical_violations_isolate_and_name_each_abnormal_field() -> None:
    weights = _weights()
    source = _source(
        original_qty=Decimal("2"),
        source_unit_net=Decimal("11"),
        source_total_net=Decimal("23"),
        source_unit_gross=Decimal("10"),
        source_total_gross=Decimal("25"),
    )

    result = weights.validate_parent_weights(
        source,
        normalized_type="板材",
        normalized_spec="10",
        normalized_width=Decimal("100"),
        density_value=Decimal("7.85"),
        density_source="plate_constant:7.85",
        theoretical_unit_weight=Decimal("10"),
        theory_basis=weights.TheoryBasis.GEOMETRY,
    )

    severe = [issue for issue in result.issues if issue.level.value == "严重"]
    assert severe
    assert all(issue.affects_part is True for issue in severe)
    fields = {issue.field for issue in severe}
    assert {"单净重", "总净重", "总毛重"}.issubset(fields)
    assert result.evidence.weight_validation_status == "severe_warning"
    assert result.evidence.weight_validation_details
