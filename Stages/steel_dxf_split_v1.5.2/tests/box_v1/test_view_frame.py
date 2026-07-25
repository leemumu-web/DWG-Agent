from __future__ import annotations

from math import cos, radians, sin
import pytest

from steel_dxf_split.box.source_ir import SourceEntityIR, build_source_ir
from steel_dxf_split.box.view_frame import build_part_views, derive_view_frame
from tests.box_v1.paths import INPUTS, PROJECT_1_INPUTS

PROJECT_1_SAMPLE = PROJECT_1_INPUTS / "w3-cb-57_拆板前.dxf"


def _transform(
    point: tuple[float, float],
    *,
    angle: float,
    offset: tuple[float, float],
) -> tuple[float, float]:
    theta = radians(angle)
    x, y = point
    return (
        x * cos(theta) - y * sin(theta) + offset[0],
        x * sin(theta) + y * cos(theta) + offset[1],
    )


def _line(
    source_id: str,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    angle: float = 0.0,
    offset: tuple[float, float] = (0.0, 0.0),
    linetype: str = "XKITLINE00",
) -> SourceEntityIR:
    return SourceEntityIR(
        source_id=source_id,
        group_id="insert:test",
        handle=source_id,
        kind="LINE",
        layer="Part",
        linetype=linetype,
        start=_transform(start, angle=angle, offset=offset),
        end=_transform(end, angle=angle, offset=offset),
    )


def _rectangle(
    *,
    angle: float = 0.0,
    offset: tuple[float, float] = (0.0, 0.0),
) -> tuple[SourceEntityIR, ...]:
    return (
        _line("bottom", (0.0, 0.0), (100.0, 0.0), angle=angle, offset=offset),
        _line("right", (100.0, 0.0), (100.0, 20.0), angle=angle, offset=offset),
        _line("top", (100.0, 20.0), (0.0, 20.0), angle=angle, offset=offset),
        _line("left", (0.0, 20.0), (0.0, 0.0), angle=angle, offset=offset),
    )


@pytest.mark.parametrize("angle", [0.0, 30.0, 89.0, 137.0])
def test_frame_recovers_longitudinal_axis_from_part_courses(angle: float) -> None:
    frame = derive_view_frame(_rectangle(angle=angle, offset=(800.0, -120.0)))

    assert frame.longitudinal_span == pytest.approx(100.0, abs=1e-8)
    assert frame.transverse_span == pytest.approx(20.0, abs=1e-8)
    assert frame.longitudinal_span > frame.transverse_span


def test_frame_is_independent_of_translation_and_entity_order() -> None:
    first = derive_view_frame(_rectangle())
    second = derive_view_frame(
        tuple(reversed(_rectangle(angle=0.0, offset=(9876.0, -4321.0))))
    )

    assert first.normalized_bounds == pytest.approx(second.normalized_bounds)
    assert first.longitudinal_span == pytest.approx(second.longitudinal_span)
    assert first.transverse_span == pytest.approx(second.transverse_span)


def test_nominal_length_resolves_a_member_shorter_than_its_section() -> None:
    entities = (
        _line("bottom", (0.0, 0.0), (80.0, 0.0)),
        _line("right", (80.0, 0.0), (80.0, 100.0)),
        _line("top", (80.0, 100.0), (0.0, 100.0)),
        _line("left", (0.0, 100.0), (0.0, 0.0)),
    )

    frame = derive_view_frame(entities, nominal_length_mm=80.0)

    assert frame.longitudinal_span == pytest.approx(80.0, abs=1e-8)
    assert frame.transverse_span == pytest.approx(100.0, abs=1e-8)


@pytest.mark.skipif(
    not PROJECT_1_SAMPLE.is_file(),
    reason="可选的项目 1 BOX 测试语料在当前机器上不可用",
)
def test_project_one_short_box_views_use_the_member_axis() -> None:
    source = build_source_ir(PROJECT_1_SAMPLE)

    views = build_part_views(source, nominal_length_mm=829.0)

    assert {round(view.frame.longitudinal_span) for view in views} == {829}
    assert {round(view.frame.transverse_span) for view in views} == {350, 900}


def test_all_real_part_groups_get_local_frames() -> None:
    for path in sorted(INPUTS.glob("*_拆板前.dxf")):
        views = build_part_views(build_source_ir(path))

        assert len(views) == 2, path.name
        assert all(
            view.frame.longitudinal_span > view.frame.transverse_span for view in views
        )
        assert all(view.entities for view in views)
        assert {view.group_id for view in views} == {
            group.group_id for group in build_source_ir(path).groups_by_layer("Part")
        }
