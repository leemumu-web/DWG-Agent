from __future__ import annotations

import pytest
from shapely.geometry import Point, box

from steel_dxf_split.part_mark_layout import (
    PartMarkLayoutError,
    PartMarkTarget,
    label_em_width,
    layout_part_marks,
    part_mark_clearance_envelope,
    part_mark_envelope,
    preferred_standard_part_mark_height,
)


def _target(
    *,
    target_id: str = "plate",
    label: str = "AB",
    outer=box(0.0, 0.0, 300.0, 100.0),
    material=None,
    hole_count: int = 0,
) -> PartMarkTarget:
    return PartMarkTarget(
        target_id=target_id,
        label=label,
        outer_geometry=outer,
        material_geometry=outer if material is None else material,
        hole_count=hole_count,
    )


def test_simsun_actual_envelope_adds_only_fixed_five_mm_clearance() -> None:
    point = (100.0, 50.0)

    assert label_em_width("A") == pytest.approx(0.6)
    assert part_mark_envelope("A", point, 30.0).bounds == pytest.approx(
        (91.0, 35.0, 109.0, 65.0)
    )
    assert part_mark_clearance_envelope("A", point, 30.0).bounds == pytest.approx(
        (86.0, 30.0, 114.0, 70.0)
    )


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [
        (200.0, 120.0),
        (89.0, 75.0),
        (30.0, 30.0),
        (29.9, 30.0),
    ],
)
def test_preferred_standard_height_never_drops_below_thirty(
    capacity: float,
    expected: float,
) -> None:
    assert preferred_standard_part_mark_height(capacity) == expected


def test_layout_preserves_preferred_height_and_uses_one_height_for_all_targets() -> None:
    placements = layout_part_marks(
        (
            _target(target_id="first", outer=box(0.0, 0.0, 500.0, 200.0)),
            _target(target_id="second", outer=box(600.0, 0.0, 1000.0, 200.0)),
        ),
        preferred_height_mm=37.5,
    )

    assert {placement.height_mm for placement in placements} == {37.5}


def test_layout_uses_material_geometry_and_avoids_a_central_hole() -> None:
    outer = box(0.0, 0.0, 300.0, 100.0)
    hole = Point(150.0, 50.0).buffer(30.0)
    material = outer.difference(hole)
    target = _target(
        label="AB",
        outer=outer,
        material=material,
        hole_count=1,
    )

    placement = layout_part_marks((target,), preferred_height_mm=30.0)[0]
    clearance = part_mark_clearance_envelope(
        target.label,
        placement.point,
        placement.height_mm,
    )

    assert material.covers(clearance)
    assert not hole.intersects(clearance)


def test_layout_reports_the_real_minimum_envelope_when_thirty_cannot_fit() -> None:
    target = _target(
        target_id="too-small",
        label="A",
        outer=box(0.0, 0.0, 20.0, 20.0),
        hole_count=2,
    )

    with pytest.raises(PartMarkLayoutError) as caught:
        layout_part_marks((target,), preferred_height_mm=30.0)

    message = str(caught.value)
    assert "too-small" in message
    assert "30 mm" in message
    assert "28.000 x 40.000 mm" in message
    assert "20.000 x 20.000 mm" in message
    assert "hole_count=2" in message
