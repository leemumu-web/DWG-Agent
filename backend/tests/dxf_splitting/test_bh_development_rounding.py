from __future__ import annotations

import pytest
from steel_dxf_split.bh_development import quantize_derived_flange_length
from steel_dxf_split.bh_knowledge import BHFlangeDevelopmentPolicy


@pytest.mark.parametrize(
    ("raw_length_mm", "expected_length_mm"),
    (
        (1138.0, 1138.0),
        (1138.001, 1139.0),
        (1138.999, 1139.0),
    ),
)
def test_derived_bh_flange_length_rounds_up_to_the_next_millimetre(
    raw_length_mm: float,
    expected_length_mm: float,
) -> None:
    assert quantize_derived_flange_length(
        raw_length_mm,
        BHFlangeDevelopmentPolicy(),
    ) == pytest.approx(expected_length_mm)
