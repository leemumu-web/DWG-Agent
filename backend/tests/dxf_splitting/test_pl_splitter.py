from __future__ import annotations

from decimal import Decimal
import importlib

import ezdxf
import pytest


def test_k_half_neutral_axis_uses_the_mean_of_both_plate_faces() -> None:
    development = importlib.import_module("steel_dxf_split.pl.development")

    assert development.neutral_axis_length((470.0, 472.0)) == pytest.approx(471.0)


def test_development_uses_the_largest_of_projection_k_and_bom_lengths() -> None:
    development = importlib.import_module("steel_dxf_split.pl.development")

    metrics = development.calculate_development(
        projection_length_mm=399.0,
        surface_lengths_mm=(468.0, 472.0),
        bom_length_mm=469.4,
        anchor_x_mm=12.0,
    )

    assert metrics.k_factor == 0.5
    assert metrics.k_length_mm == pytest.approx(470.0)
    assert metrics.raw_length_mm == pytest.approx(470.0)
    assert metrics.target_length_mm == pytest.approx(470.0)
    assert metrics.scale_x == pytest.approx(470.0 / 399.0)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Decimal("10.0"), Decimal("10.0")),
        (Decimal("10.0000004"), Decimal("10.0")),
        (Decimal("10.000002"), Decimal("10.1")),
        (Decimal("10.099999"), Decimal("10.1")),
        (Decimal("10.1000011"), Decimal("10.2")),
    ],
)
def test_length_is_ceiled_to_one_decimal_without_arbitrary_allowance(
    source: Decimal,
    expected: Decimal,
) -> None:
    development = importlib.import_module("steel_dxf_split.pl.development")

    assert development.ceil_tenth_mm(source) == expected


def test_global_x_transform_anchors_left_edge_preserves_y_and_converts_arc() -> None:
    development = importlib.import_module("steel_dxf_split.pl.development")
    document = ezdxf.new("R2007")
    line = document.modelspace().add_line((10.0, 2.0), (20.0, 2.0))
    arc = document.modelspace().add_arc((20.0, 7.0), 5.0, 270.0, 90.0)

    transformed, metrics = development.transform_outline(
        (line, arc),
        projection_length_mm=20.0,
        surface_lengths_mm=(25.0, 25.0),
        bom_length_mm=20.0,
        anchor_x_mm=10.0,
    )

    assert transformed[0].dxftype() == "LINE"
    assert transformed[0].dxf.start.x == pytest.approx(10.0)
    assert transformed[0].dxf.start.y == pytest.approx(2.0)
    assert transformed[0].dxf.end.x == pytest.approx(22.5)
    assert transformed[1].dxftype() == "ELLIPSE"
    assert metrics.scale_x == pytest.approx(1.25)
