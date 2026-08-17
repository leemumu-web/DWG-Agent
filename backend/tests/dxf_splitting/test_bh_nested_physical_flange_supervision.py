from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import ezdxf
import pytest
from shapely.geometry import Polygon, box
from steel_dxf_split.bh_compare import compare_bh_to_manual
from steel_dxf_split.bh_compiler import compile_bh_document
from steel_dxf_split.bh_knowledge import DEFAULT_TEKLA_BH_SOURCE_CONTRACT
from steel_dxf_split.bh_manufacturing_ir import (
    BHPlateIR,
    ManufacturingPlateRole,
)
from steel_dxf_split.bh_models import BHAssembly
from steel_dxf_split.dxf_io import load_document

from tests.dxf_splitting._sample_roots import diag_sample_root, require_sample

_ROOT = diag_sample_root()
_CASES = (
    (
        "3b1-cb-100",
        _ROOT / "01_原图_3b1-cb-100 (1).dxf",
        _ROOT / "3b1-cb-100手动拆分 (1).dxf",
        1391.950,
        1724.323,
    ),
    (
        "3t1-cb-102",
        _ROOT / "01_原图_3t1-cb-102 (1).dxf",
        _ROOT / "3t1-cb-102手动拆分 (1).dxf",
        1391.950,
        1724.323,
    ),
    (
        "3b1-cb-130",
        _ROOT / "01_原图_3b1-cb-130 (1).dxf",
        _ROOT / "3b1-cb-130手动拆分 (1).dxf",
        696.466,
        1028.809,
    ),
)


def _compile(path: Path):
    return compile_bh_document(
        load_document(require_sample(path)),
        source_contract=DEFAULT_TEKLA_BH_SOURCE_CONTRACT,
        source_path=path,
    )


def _physical_assembly(assembly: BHAssembly) -> BHAssembly:
    """Expand a quantity-two geometry for strict company-manual comparison."""

    flanges = [
        replace(plate, quantity=1)
        for plate in assembly.flange_plates
        for _ in range(plate.quantity)
    ]
    return BHAssembly(
        metadata=assembly.metadata,
        web_plate=replace(assembly.web_plate, quantity=1),
        flange_plates=flanges,
        retained_insert_handles=list(assembly.retained_insert_handles),
        diagnostics=dict(assembly.diagnostics),
    )


def _longitudinal_extent(plate: BHPlateIR) -> float:
    points = [
        point
        for segment in plate.outer_segments
        for point in (segment.start, segment.end)
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _source_lines(*polygons: Polygon):
    document = ezdxf.new()
    modelspace = document.modelspace()
    result = []
    for polygon in polygons:
        coordinates = tuple(polygon.exterior.coords)
        result.extend(
            modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
            for start, end in zip(coordinates, coordinates[1:], strict=False)
        )
    return result


def test_simple_trapezoid_has_two_longitudinal_rails() -> None:
    from steel_dxf_split.bh_geometry import _longitudinal_rail_lengths

    polygon = Polygon(
        ((0.0, 0.0), (991.871, 0.0), (1391.950, 400.0), (0.0, 400.0))
    )

    assert _longitudinal_rail_lengths(
        polygon,
        long_axis="x",
        tolerance_mm=0.15,
    ) == pytest.approx((991.871, 1391.950), abs=0.001)


def test_stepped_projection_has_no_simple_flange_rail_signature() -> None:
    from steel_dxf_split.bh_geometry import _longitudinal_rail_lengths

    stepped = Polygon(
        ((0.0, 0.0), (666.0, 0.0), (666.0, 95.0), (390.6, 95.0),
         (390.6, 200.0), (0.0, 200.0))
    )

    assert _longitudinal_rail_lengths(
        stepped,
        long_axis="x",
        tolerance_mm=0.15,
    ) is None


def test_source_backed_nested_trapezoids_are_two_physical_flanges() -> None:
    from steel_dxf_split.bh_geometry import (
        _recover_source_backed_nested_flange_pair,
    )

    inner = Polygon(
        ((0.0, 0.0), (991.871, 0.0), (1391.950, 400.0), (0.0, 400.0))
    )
    outer = Polygon(
        ((0.0, 0.0), (1324.244, 0.0), (1724.323, 400.0), (0.0, 400.0))
    )

    recovered = _recover_source_backed_nested_flange_pair(
        primary=outer,
        seeds=(inner,),
        entities=_source_lines(inner, outer),
        entity_source_ids=(),
        long_axis="x",
        flange_width=400.0,
        main_flange_spans={"high": 991.871, "low": 1324.244},
        grid_size=0.001,
        manufacturing_tolerance_mm=0.15,
    )

    assert recovered is not None
    primary, nested = recovered
    assert primary.equals_exact(outer, 0.001)
    assert nested.equals_exact(inner, 0.001)


def test_nested_trapezoids_require_unique_main_side_role_evidence() -> None:
    from steel_dxf_split.bh_geometry import (
        _recover_source_backed_nested_flange_pair,
    )

    inner = Polygon(
        ((0.0, 0.0), (991.871, 0.0), (1391.950, 400.0), (0.0, 400.0))
    )
    outer = Polygon(
        ((0.0, 0.0), (1324.244, 0.0), (1724.323, 400.0), (0.0, 400.0))
    )

    assert _recover_source_backed_nested_flange_pair(
        primary=outer,
        seeds=(inner,),
        entities=_source_lines(inner, outer),
        entity_source_ids=(),
        long_axis="x",
        flange_width=400.0,
        main_flange_spans={"high": 1324.244, "low": 1324.244},
        grid_size=0.001,
        manufacturing_tolerance_mm=0.15,
    ) is None


def test_complex_stepped_projection_is_not_a_physical_flange_pair() -> None:
    from steel_dxf_split.bh_geometry import (
        _recover_source_backed_nested_flange_pair,
    )

    inner = box(0.0, 0.0, 390.6, 200.0)
    stepped = Polygon(
        ((0.0, 0.0), (666.0, 0.0), (666.0, 95.0), (390.6, 95.0),
         (390.6, 200.0), (0.0, 200.0))
    )

    assert _recover_source_backed_nested_flange_pair(
        primary=stepped,
        seeds=(inner,),
        entities=_source_lines(inner, stepped),
        entity_source_ids=(),
        long_axis="x",
        flange_width=200.0,
        main_flange_spans={"high": 390.6, "low": 666.0},
        grid_size=0.001,
        manufacturing_tolerance_mm=0.15,
    ) is None


def test_nested_difference_strip_is_not_a_physical_flange() -> None:
    from steel_dxf_split.bh_geometry import (
        _recover_source_backed_nested_flange_pair,
    )

    inner = Polygon(
        ((0.0, 0.0), (296.458, 0.0), (696.466, 400.0), (0.0, 400.0))
    )
    outer = Polygon(
        ((0.0, 0.0), (628.802, 0.0), (1028.809, 400.0), (0.0, 400.0))
    )
    difference_strip = outer.difference(inner)

    assert isinstance(difference_strip, Polygon)
    assert _recover_source_backed_nested_flange_pair(
        primary=outer,
        seeds=(difference_strip,),
        entities=_source_lines(inner, outer),
        entity_source_ids=(),
        long_axis="x",
        flange_width=400.0,
        main_flange_spans={"low": 1028.809},
        grid_size=0.001,
        manufacturing_tolerance_mm=0.15,
    ) is None


def test_source_backed_parallel_terminal_web_strip_is_completed() -> None:
    from steel_dxf_split.bh_geometry import (
        _complete_parallel_terminal_web_strip,
    )

    web = Polygon(
        ((0.0, 0.0), (100.0, 0.0), (100.0, 40.0),
         (84.0, 40.0), (63.0, 55.0), (0.0, 55.0))
    )
    strip = Polygon(
        ((100.0, 40.0), (84.0, 40.0), (63.0, 55.0), (79.0, 55.0))
    )

    completed, face_index, diagnostics = _complete_parallel_terminal_web_strip(
        web,
        [strip],
        source_entities=_source_lines(web, strip),
        entity_source_ids=(),
        long_axis="x",
        web_thickness=16.0,
        grid_size=0.001,
    )

    assert face_index == 0
    assert diagnostics["applied"] is True
    assert completed.equals_exact(web.union(strip), 0.001)


@pytest.mark.parametrize(
    "web,strip,include_strip_source",
    (
        (
            Polygon(
                ((0.0, 0.0), (100.0, 0.0), (100.0, 40.0),
                 (76.0, 40.0), (55.0, 55.0), (0.0, 55.0))
            ),
            Polygon(
                ((100.0, 40.0), (76.0, 40.0), (55.0, 55.0), (79.0, 55.0))
            ),
            True,
        ),
        (
            Polygon(
                ((0.0, 0.0), (84.0, 0.0), (84.0, 40.0),
                 (63.0, 55.0), (0.0, 55.0))
            ),
            Polygon(
                ((100.0, 40.0), (84.0, 40.0), (63.0, 55.0), (79.0, 55.0))
            ),
            True,
        ),
        (
            Polygon(
                ((0.0, 0.0), (100.0, 0.0), (100.0, 40.0),
                 (84.0, 40.0), (63.0, 55.0), (0.0, 55.0))
            ),
            Polygon(
                ((100.0, 40.0), (84.0, 40.0), (63.0, 55.0), (79.0, 55.0))
            ),
            False,
        ),
        (
            Polygon(
                ((0.0, 0.0), (100.0, 0.0), (100.0, 40.0),
                 (84.0, 40.0), (63.0, 55.0), (0.0, 55.0))
            ),
            Polygon(
                ((84.0, 40.0), (63.0, 55.0), (70.0, 55.0), (84.0, 45.0))
            ),
            True,
        ),
    ),
    ids=("wrong_thickness", "bbox_change", "missing_outer_source", "real_notch"),
)
def test_terminal_web_strip_contract_rejects_unsafe_faces(
    web: Polygon,
    strip: Polygon,
    include_strip_source: bool,
) -> None:
    from steel_dxf_split.bh_geometry import (
        _complete_parallel_terminal_web_strip,
    )

    source_entities = _source_lines(web, strip) if include_strip_source else _source_lines(web)
    completed, face_index, diagnostics = _complete_parallel_terminal_web_strip(
        web,
        [strip],
        source_entities=source_entities,
        entity_source_ids=(),
        long_axis="x",
        web_thickness=16.0,
        grid_size=0.001,
    )

    assert face_index is None
    assert diagnostics["applied"] is False
    assert completed.equals_exact(web, 0.001)


@pytest.mark.parametrize(
    "sample_id,source,manual,upper_length,lower_length",
    _CASES,
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_nested_physical_flange_geometry_matches_company_manual(
    sample_id: str,
    source: Path,
    manual: Path,
    upper_length: float,
    lower_length: float,
) -> None:
    del sample_id, upper_length, lower_length
    compiled = _compile(source)

    comparison = compare_bh_to_manual(
        _physical_assembly(compiled.assembly),
        manual,
        coordinate_tolerance_mm=0.02,
        area_relative_tolerance=5.0e-5,
    )

    assert comparison.ok, comparison.to_dict()


@pytest.mark.parametrize(
    "sample_id,source,manual,upper_length,lower_length",
    _CASES,
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_nested_physical_flange_roles_are_distinct_and_correct(
    sample_id: str,
    source: Path,
    manual: Path,
    upper_length: float,
    lower_length: float,
) -> None:
    del sample_id, manual
    compiled = _compile(source)
    flanges = {
        plate.role: plate
        for plate in compiled.manufacturing_ir.plates
        if plate.role
        in {
            ManufacturingPlateRole.UPPER_FLANGE,
            ManufacturingPlateRole.LOWER_FLANGE,
        }
    }

    assert len(compiled.manufacturing_ir.plates) == 3
    assert set(flanges) == {
        ManufacturingPlateRole.UPPER_FLANGE,
        ManufacturingPlateRole.LOWER_FLANGE,
    }
    upper = flanges[ManufacturingPlateRole.UPPER_FLANGE]
    lower = flanges[ManufacturingPlateRole.LOWER_FLANGE]
    assert upper.quantity == 1
    assert lower.quantity == 1
    assert not upper.merge_authorized
    assert not lower.merge_authorized
    assert _longitudinal_extent(upper) == pytest.approx(upper_length, abs=0.02)
    assert _longitudinal_extent(lower) == pytest.approx(lower_length, abs=0.02)
    assert upper.role_evidence.state.value != "missing"
    assert lower.role_evidence.state.value != "missing"


@pytest.mark.parametrize(
    "source",
    tuple(case[1] for case in _CASES),
    ids=lambda source: source.stem,
)
def test_nested_physical_flange_keeps_all_company_web_holes(source: Path) -> None:
    compiled = _compile(source)
    web = next(
        plate
        for plate in compiled.manufacturing_ir.plates
        if plate.role == ManufacturingPlateRole.WEB
    )

    assert len(web.circular_cuts) == 16
    assert all(cut.radius_mm == pytest.approx(13.0, abs=0.02) for cut in web.circular_cuts)
