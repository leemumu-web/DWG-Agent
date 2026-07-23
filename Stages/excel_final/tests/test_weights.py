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


def test_rectangular_six_face_area_uses_all_three_dimension_pairs() -> None:
    weights = _weights()

    result = weights.rectangular_surface_area(
        Decimal("10"),
        Decimal("135"),
        Decimal("250"),
    )

    expected = Decimal("2") * (
        Decimal("10") * Decimal("135")
        + Decimal("10") * Decimal("250")
        + Decimal("135") * Decimal("250")
    ) / Decimal("1000000")
    assert result == expected


def test_writer_rounding_does_not_change_internal_values() -> None:
    weights = _weights()
    weight = Decimal("1.23456")
    area = Decimal("0.125")

    assert weights.round_weight_for_output(weight) == Decimal("1.235")
    assert weights.round_area_for_output(area) == Decimal("0.13")
    assert weight == Decimal("1.23456")
    assert area == Decimal("0.125")
