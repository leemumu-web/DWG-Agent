from __future__ import annotations

import re
from dataclasses import replace
from math import cos, radians, sin
import pytest

from steel_dxf_split.box.assembly import solve_complete_box
from steel_dxf_split.box.source_ir import (
    SourceDocumentIR,
    SourceEntityIR,
    build_source_ir,
    geometry_fingerprint,
)
from tests.box_v1.paths import INPUTS, ROOT


def _rotate_point(
    point: tuple[float, float] | None,
    *,
    angle_degrees: float,
    offset: tuple[float, float],
) -> tuple[float, float] | None:
    if point is None:
        return None
    angle = radians(angle_degrees)
    x, y = point
    return (
        x * cos(angle) - y * sin(angle) + offset[0],
        x * sin(angle) + y * cos(angle) + offset[1],
    )


def _rotate_vector(
    vector: tuple[float, float] | None,
    *,
    angle_degrees: float,
) -> tuple[float, float] | None:
    return _rotate_point(
        vector,
        angle_degrees=angle_degrees,
        offset=(0.0, 0.0),
    )


def _transform_entity(
    entity: SourceEntityIR,
    *,
    angle_degrees: float,
    offset: tuple[float, float],
) -> SourceEntityIR:
    return replace(
        entity,
        start=_rotate_point(
            entity.start,
            angle_degrees=angle_degrees,
            offset=offset,
        ),
        end=_rotate_point(
            entity.end,
            angle_degrees=angle_degrees,
            offset=offset,
        ),
        center=_rotate_point(
            entity.center,
            angle_degrees=angle_degrees,
            offset=offset,
        ),
        start_angle=(
            None
            if entity.start_angle is None
            else (entity.start_angle + angle_degrees) % 360.0
        ),
        end_angle=(
            None
            if entity.end_angle is None
            else (entity.end_angle + angle_degrees) % 360.0
        ),
        points=tuple(
            (*_rotate_point(
                (point[0], point[1]),
                angle_degrees=angle_degrees,
                offset=offset,
            ), point[2])
            for point in entity.points
        ),
        rotation=(
            None
            if entity.rotation is None
            else (entity.rotation + angle_degrees) % 360.0
        ),
        major_axis=_rotate_vector(
            entity.major_axis,
            angle_degrees=angle_degrees,
        ),
    )


def _rigidly_transform_and_reverse(source: SourceDocumentIR) -> SourceDocumentIR:
    angle_degrees = 37.0
    offset = (12_345.0, -6_789.0)
    entities = tuple(
        reversed(
            tuple(
                _transform_entity(
                    entity,
                    angle_degrees=angle_degrees,
                    offset=offset,
                )
                for entity in source.entities
            )
        )
    )
    groups = tuple(
        reversed(
            tuple(
                replace(
                    group,
                    insert_point=_rotate_point(
                        group.insert_point,
                        angle_degrees=angle_degrees,
                        offset=offset,
                    ),
                    rotation=(group.rotation + angle_degrees) % 360.0,
                )
                for group in source.groups
            )
        )
    )
    return replace(
        source,
        groups=groups,
        entities=entities,
        geometry_fingerprint=geometry_fingerprint(entities),
    )


@pytest.mark.parametrize("member", ["2b1-cb-56", "2b2-cb-145"])
def test_complete_manufacturing_ir_is_rigid_transform_and_order_invariant(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")

    baseline = solve_complete_box(source).best.mir
    transformed = solve_complete_box(_rigidly_transform_and_reverse(source)).best.mir

    assert transformed.fingerprint == baseline.fingerprint


def test_production_package_contains_no_sample_or_external_project_branches() -> None:
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/steel_dxf_split/box").rglob("*.py"))
    )
    sample_stems = {
        path.stem.removesuffix("_拆板前") for path in INPUTS.glob("*_拆板前.dxf")
    }

    assert not any(stem in source_text for stem in sample_stems)
    assert "项目1_BOX_dxf" not in source_text
    assert "项目2_BOX_dxf" not in source_text
    assert "manual_references" not in source_text
    assert "tools.manual_reference" not in source_text
    assert not re.search(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", source_text)
