from decimal import Decimal

import pytest

from steel_dxf_split.pl.development import calculate_target, ceil_tenth_mm


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Decimal("470.0"), Decimal("470.0")),
        (Decimal("470.0000001"), Decimal("470.1")),
        (Decimal("470.0999999"), Decimal("470.1")),
        (Decimal("470.1000001"), Decimal("470.2")),
    ],
)
def test_strict_tenth_ceiling_never_absorbs_a_positive_residual(
    source: Decimal,
    expected: Decimal,
) -> None:
    assert ceil_tenth_mm(source) == expected


def test_q7_b_404_uses_one_total_ceiling_without_per_interval_growth() -> None:
    target = calculate_target(
        projection_length_mm=1154.065614079,
        k_length_mm=1162.124078060,
        bom_length_mm=1162.0,
    )

    assert target.raw_length_mm == pytest.approx(1162.124078060)
    assert target.target_length_mm == pytest.approx(1162.2)
    assert target.total_extension_mm == pytest.approx(8.134385921)
    assert target.total_extension_mm < 8.2
