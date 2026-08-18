from __future__ import annotations

import os
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import box
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_extractor import _source_backed_main_flange_side_spans
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import ManufacturingPlateRole
from steel_dxf_split.dxf_io import load_document


def _part_lines(
    rows: tuple[tuple[tuple[float, float], tuple[float, float]], ...],
):
    document = ezdxf.new()
    modelspace = document.modelspace()
    return [
        modelspace.add_line(
            start,
            end,
            dxfattribs={"layer": "Part", "linetype": "Continuous"},
        )
        for start, end in rows
    ]


def test_source_course_pairs_read_the_long_plate_edge_on_each_flange_side() -> None:
    web = box(0.0, 14.0, 1000.0, 286.0)
    entities = _part_lines(
        (
            ((0.0, 0.0), (900.0, 0.0)),
            ((0.0, 14.0), (910.0, 14.0)),
            ((50.0, 286.0), (1000.0, 286.0)),
            ((60.0, 300.0), (1000.0, 300.0)),
            # The projected web edges are not flange courses.
            ((200.0, 146.0), (800.0, 146.0)),
            ((200.0, 154.0), (800.0, 154.0)),
        )
    )

    spans = _source_backed_main_flange_side_spans(
        entities,
        web,
        long_axis="x",
        flange_thickness=14.0,
        nominal_length=1000.0,
        manufacturing_tolerance_mm=0.15,
    )

    assert spans == {"low": 910.0, "high": 950.0}


def test_one_unpaired_course_cannot_authorize_a_flange_span() -> None:
    web = box(0.0, 14.0, 1000.0, 286.0)
    entities = _part_lines(
        (
            ((0.0, 0.0), (900.0, 0.0)),
            ((50.0, 286.0), (1000.0, 286.0)),
            ((60.0, 300.0), (1000.0, 300.0)),
        )
    )

    spans = _source_backed_main_flange_side_spans(
        entities,
        web,
        long_axis="x",
        flange_thickness=14.0,
        nominal_length=1000.0,
        manufacturing_tolerance_mm=0.15,
    )

    assert spans == {"high": 950.0}


def _fixture_root() -> Path:
    configured = os.environ.get("DWG_AGENT_BH_MAIN_COURSE_FIXTURE_ROOT")
    if not configured:
        pytest.skip("set DWG_AGENT_BH_MAIN_COURSE_FIXTURE_ROOT for real-DXF coverage")
    root = Path(configured)
    if not root.is_dir():
        pytest.skip(f"BH fixture root is unavailable: {root}")
    return root


def _compile(sample: int):
    source = _fixture_root() / f"BYSJ@零件图@b4-1-cb-{sample}.dxf"
    return compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )


def _flange_lengths(compiled) -> dict[ManufacturingPlateRole, float]:
    assert compiled.assessment.disposition.value == "auto_accept"
    assert compiled.manufacturing_validation.ok is True
    result: dict[ManufacturingPlateRole, float] = {}
    for plate in compiled.manufacturing_ir.plates:
        if plate.role not in {
            ManufacturingPlateRole.UPPER_FLANGE,
            ManufacturingPlateRole.LOWER_FLANGE,
        }:
            continue
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
        result[plate.role] = max(max(xs) - min(xs), max(ys) - min(ys))
        assert len(plate.outer_segments) == 4
        assert plate.quantity == 1
        assert plate.merge_authorized is False
    return result


@pytest.mark.parametrize(
    ("sample", "expected_upper", "expected_lower"),
    (
        (55, 3636.337977, 3561.0),
        (56, 2455.0, 2530.517),
        (68, 2833.0, 2919.0),
    ),
)
def test_main_course_authority_keeps_cb56_and_repairs_cb55_cb68(
    sample: int,
    expected_upper: float,
    expected_lower: float,
) -> None:
    lengths = _flange_lengths(_compile(sample))

    assert lengths == pytest.approx(
        {
            ManufacturingPlateRole.UPPER_FLANGE: expected_upper,
            ManufacturingPlateRole.LOWER_FLANGE: expected_lower,
        },
        abs=0.02,
    )


@pytest.mark.parametrize(
    ("sample", "expected_length"),
    (
        (61, 2899.628976),
        (62, 2708.355205),
        (63, 2996.847024),
        (64, 2996.567767),
        (65, 2895.080026),
        (66, 2704.080593),
    ),
)
def test_equal_source_course_pairs_emit_one_clean_quantity_two_flange(
    sample: int,
    expected_length: float,
) -> None:
    compiled = _compile(sample)

    assert compiled.assessment.disposition.value == "auto_accept"
    assert compiled.manufacturing_validation.ok is True
    assert len(compiled.assembly.flange_plates) == 1
    flange = compiled.assembly.flange_plates[0]
    assert flange.quantity == 2
    assert len(flange.contour.vertices) == 4
    assert flange.inner_contours == []

    xs = tuple(vertex.x for vertex in flange.contour.vertices)
    ys = tuple(vertex.y for vertex in flange.contour.vertices)
    assert max(max(xs) - min(xs), max(ys) - min(ys)) == pytest.approx(
        expected_length,
        abs=0.02,
    )

    flange_ir = [
        plate
        for plate in compiled.manufacturing_ir.plates
        if plate.role
        in {
            ManufacturingPlateRole.UPPER_FLANGE,
            ManufacturingPlateRole.LOWER_FLANGE,
        }
    ]
    assert len(flange_ir) == 2
    assert all(len(plate.outer_segments) == 4 for plate in flange_ir)
    assert all(plate.merge_authorized is True for plate in flange_ir)
