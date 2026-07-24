from __future__ import annotations

from decimal import Decimal

import pytest

from fabricated_profile import (
    FabricatedProfileError,
    parse_fabricated_profile,
)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (
            "BOX1000*1000*60",
            ("BOX", Decimal("1000"), Decimal("1000"), Decimal("60"), Decimal("60")),
        ),
        (
            "box1000x1000x40x60",
            ("BOX", Decimal("1000"), Decimal("1000"), Decimal("40"), Decimal("60")),
        ),
        (
            "BH500*300*12*20",
            ("BH", Decimal("500"), Decimal("300"), Decimal("12"), Decimal("20")),
        ),
        (
            "BT400*250*10*16",
            ("BT", Decimal("400"), Decimal("250"), Decimal("10"), Decimal("16")),
        ),
    ],
)
def test_parse_fabricated_profile(
    spec: str,
    expected: tuple[str, Decimal, Decimal, Decimal, Decimal],
) -> None:
    profile = parse_fabricated_profile(spec)

    assert profile is not None
    assert (
        profile.kind,
        profile.height,
        profile.width,
        profile.web_thickness,
        profile.flange_thickness,
    ) == expected


def test_box_three_parameter_geometry_is_uniform_wall() -> None:
    profile = parse_fabricated_profile("BOX100*80*10")

    assert profile is not None
    children = profile.children()
    assert children[0].part_type == "BOX腹"
    assert children[0].width == Decimal("80")
    assert children[0].quantity_multiplier == Decimal("2")
    assert children[1].part_type == "BOX翼"
    assert children[1].width == Decimal("80")
    assert children[1].quantity_multiplier == Decimal("2")
    assert profile.cross_section_area == Decimal("3200")


@pytest.mark.parametrize(
    "spec",
    [
        "BH500*300*12",
        "BT400*250*10",
        "BOX100*80",
        "BOX100*80*0",
        "BOX100*80*50",
    ],
)
def test_invalid_fabricated_geometry_is_explicit(spec: str) -> None:
    with pytest.raises(FabricatedProfileError):
        parse_fabricated_profile(spec)
