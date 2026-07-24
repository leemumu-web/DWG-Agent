from __future__ import annotations

import importlib
from decimal import Decimal

import pytest


def _weights():
    try:
        return importlib.import_module("weights")
    except ModuleNotFoundError as exc:
        pytest.fail(f"weights module is missing: {exc}")


def test_plate_and_handbook_unit_weights_use_unrounded_decimal_math() -> None:
    weights = _weights()

    plate = weights.plate_unit_weight(Decimal("7"), Decimal("13"), Decimal("17"))
    profile = weights.profile_unit_weight(Decimal("3.55"), Decimal("1234"))

    assert plate == Decimal("7") * Decimal("13") * Decimal("17") * Decimal("7.85") / Decimal("1000000")
    assert profile == Decimal("3.55") * Decimal("1234") / Decimal("1000")
    assert plate.as_tuple().exponent < -3
    assert profile.as_tuple().exponent < -3


def test_circular_hollow_linear_weight_matches_the_existing_manual_formula() -> None:
    weights = _weights()

    result = weights.circular_hollow_linear_weight(
        Decimal("2000"),
        Decimal("60"),
    )

    assert result == Decimal("2870.424")
    assert (
        weights.profile_unit_weight(result, Decimal("7291"))
        == Decimal("20928.261384")
    )


@pytest.mark.parametrize(
    ("outer_diameter", "wall_thickness"),
    [("0", "1"), ("60", "0"), ("60", "30"), ("60", "31")],
)
def test_circular_hollow_formula_rejects_nonphysical_dimensions(
    outer_diameter: str,
    wall_thickness: str,
) -> None:
    weights = _weights()

    with pytest.raises(ValueError, match="circular hollow"):
        weights.circular_hollow_linear_weight(
            Decimal(outer_diameter),
            Decimal(wall_thickness),
        )


@pytest.mark.parametrize(
    ("profile", "height", "width", "web", "flange", "length", "expected_area"),
    [
        ("BH", "700", "300", "16", "30", "3704", "28240"),
        ("BOX", "700", "700", "36", "36", "3704", "95616"),
        ("BT", "500", "300", "16", "25", "2000", "15100"),
    ],
)
def test_fabricated_parent_theory_includes_every_child_contribution(
    profile: str,
    height: str,
    width: str,
    web: str,
    flange: str,
    length: str,
    expected_area: str,
) -> None:
    weights = _weights()
    h, b, tw, tf, part_length = map(Decimal, (height, width, web, flange, length))

    result = weights.fabricated_parent_unit_weight(profile, h, b, tw, tf, part_length)
    expected = Decimal(expected_area) * part_length * Decimal("7.85") / Decimal("1000000")

    assert result == expected


def test_writer_rounding_does_not_change_internal_values() -> None:
    weights = _weights()
    weight = Decimal("1.23456")

    assert weights.round_weight_for_output(weight) == Decimal("1.235")
    assert weight == Decimal("1.23456")
