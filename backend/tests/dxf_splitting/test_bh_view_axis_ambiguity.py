"""Near-square Tekla projections keep the horizontal member axis.

A Tekla single-part drawing always lays the member along the horizontal axis.
For a member whose length equals its height, the elevation projection is
near-square and the two axis interpretations are statistically
indistinguishable.  A sub-millimetre bbox overshoot from corner arcs must not
let the web over-depth asymmetry flip the reading to the rotated axis (which
would mismatch the flange view and incur a spurious axis penalty, routing the
drawing to review).  The residual defaults to the horizontal axis instead.
"""

from __future__ import annotations

from types import SimpleNamespace

from steel_dxf_split.bh_solver import _dimension_residual


def _block(width: float, height: float) -> SimpleNamespace:
    return SimpleNamespace(bbox=SimpleNamespace(width=width, height=height))


def test_near_square_main_view_defaults_to_horizontal_axis() -> None:
    # BH700*460, length 700: the 0.002 mm overshoot from corner arcs used to
    # flip the axis to the rotated reading via the web over-depth penalty.
    residual, axis = _dimension_residual(
        _block(700.002, 700.000),
        nominal_length=700.0,
        transverse=700.0,
        role="web",
    )
    assert axis == "x"
    assert residual <= 1e-3


def test_exact_square_main_view_stays_horizontal() -> None:
    residual, axis = _dimension_residual(
        _block(800.000, 800.000),
        nominal_length=800.0,
        transverse=800.0,
        role="web",
    )
    assert axis == "x"
    assert residual == 0.0


def test_elongated_main_view_keeps_horizontal_axis() -> None:
    residual, axis = _dimension_residual(
        _block(854.000, 700.000),
        nominal_length=854.0,
        transverse=700.0,
        role="web",
    )
    assert axis == "x"
    assert residual == 0.0


def test_clear_vertical_member_keeps_vertical_axis() -> None:
    # A member drawn standing on end is unambiguous and must not be flattened.
    residual, axis = _dimension_residual(
        _block(350.000, 5600.000),
        nominal_length=5600.0,
        transverse=700.0,
        role="web",
    )
    assert axis == "y"
    # The rotated reading is a real, non-ambiguous interpretation.
    assert residual > 0.01


def test_elongated_flange_view_keeps_horizontal_axis() -> None:
    residual, axis = _dimension_residual(
        _block(700.000, 460.000),
        nominal_length=700.0,
        transverse=460.0,
        role="flange",
    )
    assert axis == "x"
    assert residual == 0.0
