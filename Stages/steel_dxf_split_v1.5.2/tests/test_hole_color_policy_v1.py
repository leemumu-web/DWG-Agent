from __future__ import annotations

from importlib import import_module

import pytest


def _policy_module():
    try:
        return import_module("steel_dxf_split.hole_color_policy")
    except ModuleNotFoundError as exc:
        pytest.fail(f"共享孔洞颜色策略模块尚未实现: {exc}")


def test_exact_mirror_pair_is_left_red_and_right_white() -> None:
    policy = _policy_module()

    plan = policy.plan_symmetric_circle_colors(
        ((10.0, 20.0, 5.0), (90.0, 20.0, 5.0)),
        plate_min_x_mm=0.0,
        plate_max_x_mm=100.0,
    )

    assert plan.colors_aci == (policy.RED_ACI, policy.WHITE_ACI)
    assert plan.pairs == ((0, 1),)
    assert plan.ambiguous_indices == ()
    assert plan.midline_indices == ()


def test_multiple_mirror_candidates_fail_closed_to_white() -> None:
    policy = _policy_module()

    plan = policy.plan_symmetric_circle_colors(
        (
            (10.0, 20.0, 5.0),
            (90.0, 20.0, 5.0),
            (90.005, 20.0, 5.0),
        ),
        plate_min_x_mm=0.0,
        plate_max_x_mm=100.0,
    )

    assert plan.colors_aci == (policy.WHITE_ACI,) * 3
    assert plan.pairs == ()
    assert plan.ambiguous_indices == (0, 1, 2)


@pytest.mark.parametrize(
    ("holes", "plate_min_x_mm", "plate_max_x_mm"),
    (
        (((float("nan"), 20.0, 5.0),), 0.0, 100.0),
        (((10.0, float("inf"), 5.0),), 0.0, 100.0),
        (((10.0, 20.0, 0.0),), 0.0, 100.0),
        (((10.0, 20.0, -1.0),), 0.0, 100.0),
        (((10.0, 20.0, 5.0),), float("nan"), 100.0),
        (((10.0, 20.0, 5.0),), 100.0, 100.0),
    ),
)
def test_invalid_manufacturing_geometry_blocks_color_planning(
    holes: tuple[tuple[float, float, float], ...],
    plate_min_x_mm: float,
    plate_max_x_mm: float,
) -> None:
    policy = _policy_module()

    with pytest.raises(ValueError):
        policy.plan_symmetric_circle_colors(
            holes,
            plate_min_x_mm=plate_min_x_mm,
            plate_max_x_mm=plate_max_x_mm,
        )


def test_tolerances_accept_only_proved_circle_mirrors() -> None:
    policy = _policy_module()

    accepted = policy.plan_symmetric_circle_colors(
        ((10.0, 20.0, 5.0), (90.003, 20.004, 5.009)),
        plate_min_x_mm=0.0,
        plate_max_x_mm=100.0,
    )
    center_outside = policy.plan_symmetric_circle_colors(
        ((10.0, 20.0, 5.0), (90.0, 20.011, 5.0)),
        plate_min_x_mm=0.0,
        plate_max_x_mm=100.0,
    )
    radius_outside = policy.plan_symmetric_circle_colors(
        ((10.0, 20.0, 5.0), (90.0, 20.0, 5.011)),
        plate_min_x_mm=0.0,
        plate_max_x_mm=100.0,
    )

    assert accepted.colors_aci == (policy.RED_ACI, policy.WHITE_ACI)
    assert center_outside.colors_aci == (policy.WHITE_ACI, policy.WHITE_ACI)
    assert radius_outside.colors_aci == (policy.WHITE_ACI, policy.WHITE_ACI)


def test_midline_and_unpaired_holes_remain_white() -> None:
    policy = _policy_module()

    plan = policy.plan_symmetric_circle_colors(
        ((10.0, 30.0, 5.0), (50.005, 30.0, 5.0)),
        plate_min_x_mm=0.0,
        plate_max_x_mm=100.0,
    )

    assert plan.colors_aci == (policy.WHITE_ACI, policy.WHITE_ACI)
    assert plan.pairs == ()
    assert plan.midline_indices == (1,)


def test_color_assignment_is_order_and_translation_invariant() -> None:
    policy = _policy_module()
    holes = (
        (10.0, 20.0, 5.0),
        (90.0, 20.0, 5.0),
        (20.0, 40.0, 6.0),
        (80.0, 40.0, 6.0),
    )
    shuffled = (holes[3], holes[2], holes[0], holes[1])
    translated = tuple((x + 1234.5, y - 300.0, radius) for x, y, radius in holes)

    original_plan = policy.plan_symmetric_circle_colors(
        holes,
        plate_min_x_mm=0.0,
        plate_max_x_mm=100.0,
    )
    shuffled_plan = policy.plan_symmetric_circle_colors(
        shuffled,
        plate_min_x_mm=0.0,
        plate_max_x_mm=100.0,
    )
    translated_plan = policy.plan_symmetric_circle_colors(
        translated,
        plate_min_x_mm=1234.5,
        plate_max_x_mm=1334.5,
    )

    original_by_geometry = dict(zip(holes, original_plan.colors_aci, strict=True))
    shuffled_by_geometry = dict(zip(shuffled, shuffled_plan.colors_aci, strict=True))
    assert shuffled_by_geometry == original_by_geometry
    original_pairs_by_geometry = tuple(
        (holes[left_index], holes[right_index])
        for left_index, right_index in original_plan.pairs
    )
    shuffled_pairs_by_geometry = tuple(
        (shuffled[left_index], shuffled[right_index])
        for left_index, right_index in shuffled_plan.pairs
    )
    assert shuffled_pairs_by_geometry == original_pairs_by_geometry
    assert translated_plan.colors_aci == original_plan.colors_aci
