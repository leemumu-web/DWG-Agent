from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import box
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_extractor import _main_flange_side_spans
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import ManufacturingPlateRole
from steel_dxf_split.dxf_io import load_document

from tests.dxf_splitting._sample_roots import (
    a1_sample_root,
    b4_sample_root,
    require_sample,
)

_A1_ROOT = a1_sample_root()
_B4_ROOT = b4_sample_root()


def test_main_flange_span_ignores_a_disconnected_thin_face() -> None:
    web = box(0.0, 20.0, 1000.0, 580.0)
    adjacent = box(0.0, 0.0, 1000.0, 20.0)
    disconnected = box(0.0, 620.0, 1000.0, 640.0)

    spans = _main_flange_side_spans(
        [adjacent, disconnected],
        web,
        long_axis="x",
        flange_thickness=20.0,
        nominal_length=1000.0,
    )

    assert spans == {"low": 1000.0}


def test_direct_projection_rectangularization_rejects_a_direct_source_notch() -> None:
    from steel_dxf_split.bh_extractor import (
        _validated_direct_projection_rectangle,
    )

    projected = box(0.0, 0.0, 1000.0, 250.0).difference(
        box(980.0, 240.0, 1000.0, 250.0)
    )
    document = ezdxf.new()
    entities = [
        document.modelspace().add_line(start, end)
        for start, end in zip(
            projected.exterior.coords,
            tuple(projected.exterior.coords)[1:],
            strict=False,
        )
    ]

    assert _validated_direct_projection_rectangle(
        projected=projected,
        entities=entities,
        entity_source_ids=(),
        projection_grid_mm=0.001,
        flange_axis="x",
        flange_width=250.0,
        main_flange_spans={"low": 1000.0},
        manufacturing_tolerance_mm=0.15,
    ) is None


def test_direct_projection_rectangle_is_the_same_geometry_that_was_verified() -> None:
    from steel_dxf_split.bh_extractor import _validated_direct_projection_rectangle

    projected = box(0.0, 0.0, 1000.0, 249.9)
    document = ezdxf.new()
    entities = [
        document.modelspace().add_line(start, end)
        for start, end in zip(
            projected.exterior.coords,
            tuple(projected.exterior.coords)[1:],
            strict=False,
        )
    ]

    assert _validated_direct_projection_rectangle(
        projected=projected,
        entities=entities,
        entity_source_ids=(),
        projection_grid_mm=0.001,
        flange_axis="x",
        flange_width=250.0,
        main_flange_spans={"low": 1000.0},
        manufacturing_tolerance_mm=0.15,
    ) is None


@pytest.mark.parametrize(
    "source",
    (
        _A1_ROOT / "BYSJ@零件图@a1-4-cb-114.dxf",
        _A1_ROOT / "BYSJ@零件图@a1-4-cb-115.dxf",
        _A1_ROOT / "BYSJ@零件图@a1-4-cb-116.dxf",
        _A1_ROOT / "BYSJ@零件图@a1-4-cb-133.dxf",
        _A1_ROOT / "BYSJ@零件图@a1-4-cb-244.dxf",
        _A1_ROOT / "BYSJ@零件图@a1-4-cb-248.dxf",
        _B4_ROOT / "b4-3-cb-2.dxf",
        _B4_ROOT / "b4-3-cb-4.dxf",
    ),
    ids=lambda source: source.stem,
)
def test_nested_projection_is_not_materialized_as_a_stepped_flange(
    source: Path,
) -> None:
    """A nested drawing projection must not become a second physical plate."""

    compiled = compile_bh_document(
        load_document(require_sample(source)),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    flanges = tuple(
        plate
        for plate in compiled.manufacturing_ir.plates
        if plate.role
        in {
            ManufacturingPlateRole.UPPER_FLANGE,
            ManufacturingPlateRole.LOWER_FLANGE,
        }
    )

    assert len(flanges) == 2
    assert all(len(plate.outer_segments) == 4 for plate in flanges)


def test_same_length_flange_projection_drops_the_assembly_end_step() -> None:
    source = _A1_ROOT / "BYSJ@零件图@a1-4-cb-117.dxf"

    compiled = compile_bh_document(
        load_document(require_sample(source)),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )
    flanges = tuple(
        plate
        for plate in compiled.manufacturing_ir.plates
        if plate.role
        in {
            ManufacturingPlateRole.UPPER_FLANGE,
            ManufacturingPlateRole.LOWER_FLANGE,
        }
    )

    assert len(flanges) == 2
    assert all(len(plate.outer_segments) == 4 for plate in flanges)
    for plate in flanges:
        xs = tuple(
            coordinate
            for segment in plate.outer_segments
            for coordinate in (segment.start[0], segment.end[0])
        )
        ys = tuple(
            coordinate
            for segment in plate.outer_segments
            for coordinate in (segment.start[1], segment.end[1])
        )
        assert max(xs) - min(xs) == pytest.approx(7600.0)
        assert max(ys) - min(ys) == pytest.approx(250.0)


def test_notched_web_proves_its_unique_weld_allowance_terminal() -> None:
    source = _A1_ROOT / "BYSJ@零件图@a1-4-cb-126.dxf"

    compiled = compile_bh_document(
        load_document(require_sample(source)),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )

    web = next(
        plate
        for plate in compiled.manufacturing_ir.plates
        if plate.role == ManufacturingPlateRole.WEB
    )
    assert web.weld_allowance_contract is not None
    assert web.weld_allowance_contract.movable_end == "positive_x"
    assert web.weld_allowance_contract.rail_segment_ids == (
        "web:outer:0000",
        "web:outer:0012",
    )
    assert web.weld_allowance_contract.positive_terminal_segment_ids == tuple(
        f"web:outer:{index:04d}" for index in range(13, 20)
    )
