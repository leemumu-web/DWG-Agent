from __future__ import annotations

import importlib
from decimal import Decimal

import pytest


def _parser():
    try:
        return importlib.import_module("spec_parser")
    except ModuleNotFoundError as exc:
        pytest.fail(f"spec parser module is missing: {exc}")


@pytest.mark.parametrize(
    ("spec", "width"),
    [
        ("PL6*30", None),
        ("6", "30"),
    ],
)
def test_pl_6_by_30_is_the_explicit_flat_steel_exception(spec: str, width: str | None) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec(spec, material="Q355B", width=width)

    assert result.normalized_type == "扁钢"
    assert result.normalized_spec == "6*30"
    assert result.normalized_width is None
    assert result.handbook_category is parser.HandbookCategory.FLAT_STEEL
    assert result.lookup_policy is parser.LookupPolicy.HANDBOOK
    assert result.split_policy is parser.SplitPolicy.NONE


@pytest.mark.parametrize(
    ("spec", "expected_spec", "expected_width"),
    [
        ("PL50*6", "50", Decimal("6")),
        ("PL6*50", "6", Decimal("50")),
        ("-20*145", "20", Decimal("145")),
    ],
)
def test_other_explicit_plate_keeps_written_dimension_order(
    spec: str,
    expected_spec: str,
    expected_width: Decimal,
) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec(spec, material="Q355B")

    assert result.normalized_type == "板材"
    assert result.normalized_spec == expected_spec
    assert result.normalized_width == expected_width
    assert result.handbook_category is None
    assert result.lookup_policy is parser.LookupPolicy.PLATE_CONSTANT
    assert parser.parse_plate_dims(spec) == (float(expected_spec), float(expected_width))


@pytest.mark.parametrize("spec", ["FB6*30", "FLAT 6X30", "扁钢6×30", "扁铁6*30"])
def test_explicit_flat_prefixes_have_one_flat_steel_category(spec: str) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec(spec, material="Q355B")

    assert result.normalized_type == "扁钢"
    assert result.normalized_spec == "6*30"
    assert result.normalized_width is None
    assert result.handbook_category is parser.HandbookCategory.FLAT_STEEL
    assert result.lookup_policy is parser.LookupPolicy.HANDBOOK


def test_bare_dimensions_are_flat_first_candidates_not_immediate_plates() -> None:
    parser = _parser()

    result = parser.classify_normalized_spec("10*143", material="Q355B")

    assert result.normalized_type == "扁钢候选"
    assert result.normalized_spec == "10*143"
    assert result.normalized_width is None
    assert result.handbook_category is parser.HandbookCategory.FLAT_STEEL
    assert result.lookup_policy is parser.LookupPolicy.FLAT_THEN_PLATE


@pytest.mark.parametrize(
    ("spec", "normalized_type", "split_policy"),
    [
        ("BH700*300*16*30", "BH", "BH"),
        ("BOX700*700*36*36", "BOX", "BOX"),
        ("BT500*300*16*25", "BT", "BT"),
    ],
)
def test_only_bh_box_bt_are_split_candidates(
    spec: str,
    normalized_type: str,
    split_policy: str,
) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec(spec, material="Q355B")

    assert result.normalized_type == normalized_type
    assert result.normalized_spec == spec
    assert result.handbook_category is None
    assert result.lookup_policy is parser.LookupPolicy.PLATE_CONSTANT
    assert result.split_policy.value == split_policy


@pytest.mark.parametrize(
    ("spec", "expected_normalized"),
    [
        ("BBH700~500*300*16*30", "BBH700-500*300*16*30"),
        ("BBH700-500*300*16*30", "BBH700-500*300*16*30"),
        ("BBH600*200*12*22", "BBH600*200*12*22"),
    ],
)
def test_bbh_is_a_split_candidate_with_variable_height(
    spec: str,
    expected_normalized: str,
) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec(spec, material="Q355B")

    assert result.normalized_type == "BBH"
    assert result.normalized_spec == expected_normalized
    assert result.handbook_category is None
    assert result.lookup_policy is parser.LookupPolicy.PLATE_CONSTANT
    assert result.split_policy.value == "BBH"


def test_ordinary_i_is_handbook_profile_and_ha_is_unsupported() -> None:
    parser = _parser()

    i_result = parser.classify_normalized_spec("I20a", material="Q355B")
    ha_result = parser.classify_normalized_spec("HA700*300*16*30", material="Q355B")

    assert i_result.normalized_type == "工字钢"
    assert i_result.handbook_category is parser.HandbookCategory.I_BEAM
    assert i_result.lookup_policy is parser.LookupPolicy.HANDBOOK
    assert i_result.split_policy is parser.SplitPolicy.NONE
    assert ha_result.normalized_type == "未分类"
    assert ha_result.handbook_category is None
    assert ha_result.lookup_policy is parser.LookupPolicy.NOT_FOUND
    assert ha_result.split_policy is parser.SplitPolicy.NONE


@pytest.mark.parametrize(
    ("material", "normalized_type", "category", "policy"),
    [
        ("HPB300", "圆钢", "ROUND_BAR", "HANDBOOK"),
        ("Q235B", "圆钢", "ROUND_BAR", "HANDBOOK"),
        ("Q355B", "圆钢", "ROUND_BAR", "HANDBOOK"),
        ("HRB400", "螺纹钢", "REBAR", "HANDBOOK"),
        ("Q420B", "未分类", None, "NOT_FOUND"),
        ("", "未分类", None, "NOT_FOUND"),
    ],
)
def test_d_series_classification_is_material_aware(
    material: str,
    normalized_type: str,
    category: str | None,
    policy: str,
) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec("D24", material=material)

    assert result.normalized_type == normalized_type
    assert result.normalized_spec == "24"
    assert result.handbook_category is (
        None if category is None else getattr(parser.HandbookCategory, category)
    )
    assert result.lookup_policy is getattr(parser.LookupPolicy, policy)
    if category is None:
        assert result.reason == "D系列材质不足"


@pytest.mark.parametrize(
    ("spec", "normalized_type"),
    [
        ("M24", "螺栓"),
        ("NUT_M24", "螺母"),
        ("螺套M24", "螺套"),
        ("SLEEVE_M24", "螺套"),
        ("TT25", "TT"),
    ],
)
def test_fasteners_sleeves_and_tt_are_explicitly_skipped(
    spec: str,
    normalized_type: str,
) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec(spec, material="Q355B")

    assert result.normalized_type == normalized_type
    assert result.handbook_category is None
    assert result.lookup_policy is parser.LookupPolicy.SKIP
    assert result.split_policy is parser.SplitPolicy.NONE


def test_blank_spec_uses_explicit_fastener_part_number_only() -> None:
    parser = _parser()

    bolt = parser.classify_normalized_spec(
        "",
        material="TS10.9",
        part_no="M22",
    )
    ambiguous = parser.classify_normalized_spec(
        "",
        material="",
        part_no="D19",
    )

    assert bolt.normalized_type == "螺栓"
    assert bolt.normalized_spec == "M22"
    assert bolt.lookup_policy is parser.LookupPolicy.SKIP
    assert ambiguous.normalized_type == "未分类"
    assert ambiguous.lookup_policy is parser.LookupPolicy.NOT_FOUND


@pytest.mark.parametrize(
    ("spec", "category"),
    [
        ("HN300*150*6.5*9", "H_BEAM"),
        ("HW300*300*10*15", "H_BEAM"),
        ("HM300*200*8*12", "H_BEAM"),
        ("HT300*150*6.5*9", "H_BEAM"),
        ("C20a", "CHANNEL"),
        ("L50*5", "ANGLE"),
        ("方管100*100*5", "SQUARE_TUBE"),
        ("IP60*3.5", "STEEL_PIPE"),
        ("TN100*100*6*8", "T_BEAM"),
        ("LH100*50*2.3*3.2", "HFW_PIPE"),
        ("HFW100*50*2.3*3.2", "HFW_PIPE"),
        ("W4X13", "W_BEAM"),
    ],
)
def test_other_known_profiles_map_to_exactly_one_handbook_category(
    spec: str,
    category: str,
) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec(spec, material="Q355B")

    assert result.handbook_category is getattr(parser.HandbookCategory, category)
    assert result.lookup_policy is parser.LookupPolicy.HANDBOOK
    assert result.split_policy is parser.SplitPolicy.NONE


@pytest.mark.parametrize(
    ("spec", "expected_outer_diameter", "expected_wall_thickness"),
    [
        ("PIP2000*60", "2000", Decimal("60")),
        ("PD114.3×6.3", "114.3", Decimal("6.3")),
    ],
)
def test_pip_and_pd_use_circular_hollow_formula_instead_of_handbook(
    spec: str,
    expected_outer_diameter: str,
    expected_wall_thickness: Decimal,
) -> None:
    parser = _parser()

    result = parser.classify_normalized_spec(spec, material="Q355B")

    assert result.normalized_type == "圆管"
    assert result.normalized_spec == expected_outer_diameter
    assert result.normalized_width == expected_wall_thickness
    assert result.handbook_category is None
    assert result.lookup_policy is parser.LookupPolicy.CIRCULAR_HOLLOW_FORMULA
    assert result.split_policy is parser.SplitPolicy.NONE


def test_canonical_classifier_does_not_guess_d_series_material_or_ha_profiles() -> None:
    parser = _parser()

    assert not hasattr(parser, "D8_DENSITY")
    d_series = parser.classify_normalized_spec("D24")
    ha_profile = parser.classify_normalized_spec("HA700*300*16*30")
    i_profile = parser.classify_normalized_spec("I20a")
    assert d_series.normalized_type == "未分类"
    assert d_series.reason == "D系列材质不足"
    assert ha_profile.normalized_type == "未分类"
    assert ha_profile.reason == "HA不在支持范围"
    assert i_profile.normalized_type == "工字钢"
    assert i_profile.handbook_category is parser.HandbookCategory.I_BEAM
