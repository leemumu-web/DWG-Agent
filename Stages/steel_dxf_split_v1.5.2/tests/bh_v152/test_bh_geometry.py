from __future__ import annotations

from steel_dxf_split.bh_geometry import _simplify_collinear
from steel_dxf_split.bh_models import BulgeVertex


def test_near_collinear_reversal_hair_is_not_a_physical_boundary() -> None:
    vertices = [
        BulgeVertex(0.0, 0.0),
        BulgeVertex(100.0, 0.005),
        BulgeVertex(90.0, 0.005),
        BulgeVertex(90.0, 50.0),
        BulgeVertex(0.0, 50.0),
    ]

    simplified = _simplify_collinear(vertices)

    assert BulgeVertex(100.0, 0.005) not in simplified
    assert BulgeVertex(90.0, 0.005) in simplified


def test_manufacturing_scale_notch_is_not_simplified_as_line_noise() -> None:
    vertices = [
        BulgeVertex(0.0, 0.0),
        BulgeVertex(100.0, 1.0),
        BulgeVertex(90.0, 1.0),
        BulgeVertex(90.0, 50.0),
        BulgeVertex(0.0, 50.0),
    ]

    assert _simplify_collinear(vertices) == vertices


def test_forward_shallow_kink_is_not_simplified_as_line_noise() -> None:
    vertices = [
        BulgeVertex(0.0, 0.0),
        BulgeVertex(100.0, 0.005),
        BulgeVertex(110.0, 0.0),
        BulgeVertex(110.0, 50.0),
        BulgeVertex(0.0, 50.0),
    ]

    assert _simplify_collinear(vertices) == vertices


def test_exact_forward_collinear_subdivision_is_removed() -> None:
    subdivision = BulgeVertex(100.0, 0.0)
    vertices = [
        BulgeVertex(0.0, 0.0),
        subdivision,
        BulgeVertex(110.0, 0.0),
        BulgeVertex(110.0, 50.0),
        BulgeVertex(0.0, 50.0),
    ]

    simplified = _simplify_collinear(vertices)

    assert subdivision not in simplified
    assert simplified == [vertices[0], *vertices[2:]]
