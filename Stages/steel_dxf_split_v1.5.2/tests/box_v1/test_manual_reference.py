from __future__ import annotations

from shapely.geometry import Point, Polygon

from steel_dxf_split.box.compiler import compile_box_core
from steel_dxf_split.box.contracts import BoxSourceContract
from steel_dxf_split.box.manufacturing_ir import contour_polygon
from steel_dxf_split.box.writer import layout_box_manufacturing_ir
from tests.box_v1.paths import INPUTS, REFERENCES
from tools.box_manual_reference import load_manual_reference


def test_manual_region_extractor_preserves_acis_curves() -> None:
    reference = load_manual_reference(
        REFERENCES / "2b2-cb-145_拆板后.dxf"
    )
    curved = tuple(
        plate.shape
        for plate in reference.plates
        if len(plate.shape.sampled_points) > len(plate.shape.vertices)
    )

    assert curved
    assert any(
        abs(shape.area - Polygon(shape.vertices).area) > 0.5
        for shape in curved
    )


def test_output_marks_use_frozen_v1_mir_before_manual_reference() -> None:
    source_path = INPUTS / "2b1-cb-56_拆板前.dxf"
    core = compile_box_core(source_path, BoxSourceContract())
    frozen_fingerprint = core.fingerprint
    layout = layout_box_manufacturing_ir(core.manufacturing)

    reference = load_manual_reference(
        REFERENCES / "2b1-cb-56_拆板后.dxf"
    )

    assert core.fingerprint == frozen_fingerprint
    assert reference.member_mark == core.metadata.member_mark.value
    assert {plate.label for plate in layout.plates} == {
        f"p={reference.member_mark}腹",
        f"p={reference.member_mark}翼",
    }
    assert all(
        contour_polygon(plate.outer_segments).covers(Point(point))
        for plate, point in zip(
            layout.plates,
            layout.label_points,
            strict=True,
        )
    )
