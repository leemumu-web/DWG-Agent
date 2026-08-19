from __future__ import annotations

import os
from pathlib import Path

import pytest
from shapely.geometry import Polygon
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_extractor import _materialize_constant_height_flange_paths
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.dxf_io import load_document


def _has_slanted_edge(coordinates: list[tuple[float, float]]) -> bool:
    return any(
        abs(end[0] - start[0]) > 0.01 and abs(end[1] - start[1]) > 0.01
        for start, end in zip(coordinates, coordinates[1:] + coordinates[:1], strict=False)
    )


def test_two_path_materialization_moves_only_the_slanted_terminal_cap() -> None:
    source = Polygon(
        (
            (0.0, 500.0),
            (58.0, 500.0),
            (5832.28, 500.0),
            (5722.515, 0.0),
            (0.0, 0.0),
        )
    )

    result = _materialize_constant_height_flange_paths(
        source,
        (5749.0, 5832.28),
        flange_axis="x",
        flange_width=500.0,
        preserve_slanted_end=True,
    )

    assert all(polygon.is_valid for polygon in result)
    assert [polygon.bounds[2] - polygon.bounds[0] for polygon in result] == pytest.approx(
        (5749.0, 5832.28), abs=0.001
    )
    assert all(_has_slanted_edge(list(polygon.exterior.coords)[:-1]) for polygon in result)
    assert (58.0, 500.0) in list(result[0].exterior.coords)


def test_two_path_materialization_keeps_orthogonal_projection_rectangular() -> None:
    source = Polygon(
        (
            (0.0, 0.0),
            (1000.0, 0.0),
            (1000.0, 200.0),
            (50.0, 200.0),
            (50.0, 150.0),
            (0.0, 150.0),
        )
    )

    result = _materialize_constant_height_flange_paths(
        source,
        (900.0, 1000.0),
        flange_axis="x",
        flange_width=200.0,
        preserve_slanted_end=False,
    )

    assert all(len(list(polygon.exterior.coords)) - 1 == 4 for polygon in result)


@pytest.mark.parametrize(
    "filename",
    (
        "BYSJ@零件图@3t1-cb-13_拆板前.dxf",
        "BYSJ@板零件图@5t1-cb-51_拆板前.dxf",
        "BYSJ@零件图@4t1-cb-39_拆板前.dxf",
    ),
)
def test_real_two_path_flange_keeps_its_slanted_end(filename: str) -> None:
    fixture_root = os.environ.get("DWG_AGENT_BH_SLANTED_FLANGE_FIXTURE_ROOT")
    if not fixture_root:
        pytest.skip("set DWG_AGENT_BH_SLANTED_FLANGE_FIXTURE_ROOT for real-DXF coverage")
    source = Path(fixture_root) / filename
    if not source.is_file():
        pytest.skip(f"fixture unavailable: {source}")

    compiled = compile_bh_document(
        load_document(source),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=source,
    )

    assert compiled.assessment.disposition.value == "auto_accept"
    assert len(compiled.assembly.flange_plates) == 2
    assert all(
        _has_slanted_edge([(vertex.x, vertex.y) for vertex in plate.contour.vertices])
        for plate in compiled.assembly.flange_plates
    )
