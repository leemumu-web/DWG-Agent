from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import ezdxf
import pytest
from steel_dxf_split_pl.contracts import PLSplitError

from tests.support.paths import REPO_ROOT


def test_k_half_neutral_axis_uses_the_mean_of_both_plate_faces() -> None:
    development = importlib.import_module("steel_dxf_split_pl.development")

    assert development.neutral_axis_length((470.0, 472.0)) == pytest.approx(471.0)


def test_development_target_uses_the_largest_of_projection_k_and_bom_lengths() -> None:
    development = importlib.import_module("steel_dxf_split_pl.development")

    target = development.calculate_target(
        projection_length_mm=399.0,
        k_length_mm=470.0,
        bom_length_mm=469.4,
    )

    assert target.k_length_mm == pytest.approx(470.0)
    assert target.raw_length_mm == pytest.approx(470.0)
    assert target.target_length_mm == pytest.approx(470.0)
    assert target.total_extension_mm == pytest.approx(71.0)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Decimal("10.0"), Decimal("10.0")),
        (Decimal("10.0000004"), Decimal("10.1")),
        (Decimal("10.000002"), Decimal("10.1")),
        (Decimal("10.099999"), Decimal("10.1")),
        (Decimal("10.1000011"), Decimal("10.2")),
    ],
)
def test_length_is_ceiled_to_one_decimal_without_arbitrary_allowance(
    source: Decimal,
    expected: Decimal,
) -> None:
    development = importlib.import_module("steel_dxf_split_pl.development")

    assert development.ceil_tenth_mm(source) == expected


def _new_source_document() -> ezdxf.document.Drawing:
    document = ezdxf.new("R2007")
    document.header["$INSUNITS"] = 4
    for layer in ("Part", "PartMark", "OtherObjectType"):
        document.layers.add(layer)
    return document


def _add_metadata(
    layout,
    *,
    part_number: str,
    specification: str,
    bom_length: str,
    y: float = 0.0,
) -> None:
    layout.add_text(part_number, dxfattribs={"layer": "PartMark", "height": 10}).dxf.insert = (
        20.0,
        50.0,
    )
    layout.add_text(
        specification, dxfattribs={"layer": "OtherObjectType", "height": 10}
    ).dxf.insert = (
        100.0,
        y,
    )
    layout.add_text(
        bom_length, dxfattribs={"layer": "OtherObjectType", "height": 10}
    ).dxf.insert = (
        200.0,
        y,
    )


def _save_combined_metadata_dxf(path: Path) -> None:
    document = _new_source_document()
    first = document.blocks.new("sheet-a")
    _add_metadata(
        first,
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="470",
    )
    second = document.blocks.new("sheet-b")
    _add_metadata(
        second,
        part_number="p=q6-b-71",
        specification="PL16×350",
        bom_length="1258.1",
    )
    document.modelspace().add_blockref("sheet-a", (0.0, 0.0))
    document.modelspace().add_blockref("sheet-b", (1000.0, 0.0))
    document.saveas(path)


def test_combined_source_expands_each_top_level_sheet_and_binds_its_metadata(
    tmp_path: Path,
) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    drawing = tmp_path / "combined.dxf"
    _save_combined_metadata_dxf(drawing)

    contexts = source.load_source_contexts(drawing)
    first = source.extract_metadata(contexts[0])
    second = source.extract_metadata(contexts[1])

    assert tuple(context.context_id for context in contexts) == ("sheet-a", "sheet-b")
    assert (first.part_number, first.thickness_mm, first.width_mm, first.bom_length_mm) == (
        "q6-b-62",
        25.0,
        300.0,
        470.0,
    )
    assert (second.part_number, second.thickness_mm, second.width_mm) == (
        "q6-b-71",
        16.0,
        350.0,
    )
    assert second.bom_length_mm == pytest.approx(1258.1)


def test_part_number_parser_strips_only_the_p_equals_prefix() -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")

    assert source.canonical_part_number("p=q6-b-62") == "q6-b-62"
    assert source.canonical_part_number("Q6-B-62") == "Q6-B-62"


def test_conflicting_part_marks_are_rejected_instead_of_using_the_filename(
    tmp_path: Path,
) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    drawing = tmp_path / "misleading-name.dxf"
    document = _new_source_document()
    _add_metadata(
        document.modelspace(),
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="470",
    )
    document.modelspace().add_text(
        "q6-b-63",
        dxfattribs={"layer": "PartMark", "height": 10},
    )
    document.saveas(drawing)

    [context] = source.load_source_contexts(drawing)
    with pytest.raises(PLSplitError) as error:
        source.extract_metadata(context)

    assert error.value.code == "PART_NUMBER_AMBIGUOUS"


def test_missing_part_mark_uses_the_unique_part_number_on_the_pl_row(
    tmp_path: Path,
) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    drawing = tmp_path / "missing-part-mark.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    layout.add_text(
        "q6-b-62", dxfattribs={"layer": "OtherObjectType", "height": 10}
    ).dxf.insert = (20.0, 0.5)
    layout.add_text(
        "PL25*300", dxfattribs={"layer": "OtherObjectType", "height": 10}
    ).dxf.insert = (100.0, 0.0)
    layout.add_text(
        "470", dxfattribs={"layer": "OtherObjectType", "height": 10}
    ).dxf.insert = (200.0, 0.0)
    document.saveas(drawing)

    [context] = source.load_source_contexts(drawing)
    metadata = source.extract_metadata(context)

    assert metadata.part_number == "q6-b-62"


def test_part_mark_remains_authoritative_over_a_pl_row_part_number(
    tmp_path: Path,
) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    drawing = tmp_path / "part-mark-authority.dxf"
    document = _new_source_document()
    _add_metadata(
        document.modelspace(),
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="470",
    )
    document.modelspace().add_text(
        "q6-b-99", dxfattribs={"layer": "OtherObjectType", "height": 10}
    ).dxf.insert = (20.0, 0.0)
    document.saveas(drawing)

    [context] = source.load_source_contexts(drawing)
    metadata = source.extract_metadata(context)

    assert metadata.part_number == "q6-b-62"


def test_multiple_pl_row_part_numbers_are_rejected(tmp_path: Path) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    drawing = tmp_path / "ambiguous-pl-row-part-number.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    for value, x in (("q6-b-62", 20.0), ("q6-b-63", 40.0)):
        layout.add_text(
            value, dxfattribs={"layer": "OtherObjectType", "height": 10}
        ).dxf.insert = (x, 0.0)
    layout.add_text(
        "PL25*300", dxfattribs={"layer": "OtherObjectType", "height": 10}
    ).dxf.insert = (100.0, 0.0)
    layout.add_text(
        "470", dxfattribs={"layer": "OtherObjectType", "height": 10}
    ).dxf.insert = (200.0, 0.0)
    document.saveas(drawing)

    [context] = source.load_source_contexts(drawing)
    with pytest.raises(PLSplitError) as error:
        source.extract_metadata(context)

    assert error.value.code == "PART_NUMBER_AMBIGUOUS"


def test_multiple_pl_rows_are_rejected(tmp_path: Path) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    drawing = tmp_path / "multiple.dxf"
    document = _new_source_document()
    _add_metadata(
        document.modelspace(),
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="470",
    )
    _add_metadata(
        document.modelspace(),
        part_number="q6-b-62",
        specification="PL16*350",
        bom_length="1258",
        y=-50.0,
    )
    document.saveas(drawing)

    [context] = source.load_source_contexts(drawing)
    with pytest.raises(PLSplitError) as error:
        source.extract_metadata(context)

    assert error.value.code == "PL_SPEC_AMBIGUOUS"


def test_ambiguous_bom_cells_on_the_pl_row_are_rejected(tmp_path: Path) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    drawing = tmp_path / "ambiguous-length.dxf"
    document = _new_source_document()
    _add_metadata(
        document.modelspace(),
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="470",
    )
    document.modelspace().add_text(
        "471",
        dxfattribs={"layer": "OtherObjectType", "height": 10},
    ).dxf.insert = (200.0, 0.0)
    document.saveas(drawing)

    [context] = source.load_source_contexts(drawing)
    with pytest.raises(PLSplitError) as error:
        source.extract_metadata(context)

    assert error.value.code == "BOM_LENGTH_AMBIGUOUS"


def test_input_discovery_is_frozen_sorted_and_first_level_only(tmp_path: Path) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    nested = input_dir / "nested"
    nested.mkdir()
    for path in (input_dir / "b.dxf", input_dir / "A.DXF", nested / "ignored.dxf"):
        path.write_text("placeholder", encoding="ascii")

    discovered = source.discover_input_files(input_dir, output_dir)
    (input_dir / "later.dxf").write_text("placeholder", encoding="ascii")

    assert tuple(path.name for path in discovered) == ("A.DXF", "b.dxf")


@pytest.mark.parametrize("relation", ["same", "output-inside-input", "input-inside-output"])
def test_input_and_output_directories_must_be_disjoint(
    tmp_path: Path,
    relation: str,
) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    if relation == "same":
        input_dir = output_dir = tmp_path / "shared"
        input_dir.mkdir()
    elif relation == "output-inside-input":
        input_dir = tmp_path / "input"
        output_dir = input_dir / "output"
        input_dir.mkdir()
    else:
        output_dir = tmp_path / "output"
        input_dir = output_dir / "input"
        input_dir.mkdir(parents=True)

    with pytest.raises(PLSplitError) as error:
        source.discover_input_files(input_dir, output_dir)

    assert error.value.code == "INPUT_OUTPUT_OVERLAP"


def test_dwg_input_is_rejected_with_conversion_guidance(tmp_path: Path) -> None:
    source = importlib.import_module("steel_dxf_split_pl.source")
    drawing = tmp_path / "source.dwg"
    drawing.write_bytes(b"not-a-dwg")

    with pytest.raises(PLSplitError) as error:
        source.load_source_contexts(drawing)

    assert error.value.code == "DWG_NOT_SUPPORTED"


def _add_closed_polygon(layout, points: tuple[tuple[float, float], ...]) -> None:
    for start, end in zip(points, (*points[1:], points[0]), strict=True):
        layout.add_line(start, end, dxfattribs={"layer": "Part"})


def _save_geometry_source(
    path: Path,
    *,
    include_section: bool = True,
    duplicate_main: bool = False,
    include_hole: bool = False,
    small_circle_offset_mm: float | None = None,
) -> None:
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="470",
        y=-900.0,
    )
    _add_closed_polygon(layout, ((0.0, 0.0), (399.0, 0.0), (399.0, 300.0), (0.0, 300.0)))
    layout.add_line((100.0, 0.0), (100.0, 300.0), dxfattribs={"layer": "Part"})
    if duplicate_main:
        _add_closed_polygon(
            layout,
            ((0.0, 500.0), (399.0, 500.0), (399.0, 800.0), (0.0, 800.0)),
        )
    if include_hole:
        layout.add_circle(
            (110.0, 110.0),
            10.0,
            dxfattribs={"layer": "Bolt"},
        )
    if small_circle_offset_mm is not None:
        center = (200.0 + small_circle_offset_mm, 110.0)
        layout.add_arc(
            center,
            10.0,
            0.0,
            180.0,
            dxfattribs={"layer": "Part"},
        )
        layout.add_arc(
            center,
            10.0,
            180.0,
            360.0,
            dxfattribs={"layer": "Part"},
        )
        layout.add_circle(
            (200.0, 110.0),
            20.0,
            dxfattribs={"layer": "Bolt"},
        )
    if include_section:
        _add_closed_polygon(
            layout,
            ((0.0, -500.0), (399.0, -500.0), (399.0, -475.0), (0.0, -475.0)),
        )
        layout.add_line(
            (200.0, -500.0),
            (200.0, -475.0),
            dxfattribs={"layer": "Part"},
        )
    document.saveas(path)


def _load_geometry_context(path: Path):
    source = importlib.import_module("steel_dxf_split_pl.source")
    [context] = source.load_source_contexts(path)
    return context, source.extract_metadata(context)


def test_geometry_proof_selects_only_the_main_outer_boundary_and_unique_section(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "geometry.dxf"
    _save_geometry_source(drawing)
    context, metadata = _load_geometry_context(drawing)

    outline, section = geometry.analyze_geometry(context, metadata)

    assert outline.projection_length_mm == pytest.approx(399.0)
    assert outline.width_mm == pytest.approx(300.0)
    assert outline.anchor_x_mm == pytest.approx(0.0)
    assert len(outline.outer_entities) == 4
    assert section.k_length_mm == pytest.approx(399.0)
    assert section.proof_method == "section_area_over_thickness_k_half"


def test_section_area_over_thickness_handles_a_sloped_end_cap(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "sloped-cap.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="400",
        y=-900.0,
    )
    _add_closed_polygon(layout, ((0.0, 0.0), (400.0, 0.0), (400.0, 300.0), (0.0, 300.0)))
    _add_closed_polygon(
        layout,
        ((0.0, -500.0), (390.0, -500.0), (400.0, -475.0), (0.0, -475.0)),
    )
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    _, section = geometry.analyze_geometry(context, metadata)

    assert section.k_length_mm == pytest.approx(395.0)


def test_main_view_hole_is_retained_as_a_cutout_group(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "hole.dxf"
    _save_geometry_source(drawing, include_hole=True)
    context, metadata = _load_geometry_context(drawing)

    outline, _ = geometry.analyze_geometry(context, metadata)

    assert len(outline.cutout_entity_groups) == 1
    assert len(outline.cutout_entity_groups[0]) == 2


def test_large_circle_covered_small_center_keeps_only_outer(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "covered-center-hole.dxf"
    _save_geometry_source(drawing, small_circle_offset_mm=10.3)
    context, metadata = _load_geometry_context(drawing)

    outline, _ = geometry.analyze_geometry(context, metadata)

    assert len(outline.cutout_entity_groups) == 1
    polygon = geometry.validate_closed_outline(outline.cutout_entity_groups[0])
    assert polygon.bounds == pytest.approx((180.0, 90.0, 220.0, 130.0), abs=0.001)


def test_small_center_outside_large_circle_keeps_both(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "offset-holes.dxf"
    _save_geometry_source(drawing, small_circle_offset_mm=20.2)
    context, metadata = _load_geometry_context(drawing)

    outline, _ = geometry.analyze_geometry(context, metadata)

    assert len(outline.cutout_entity_groups) == 2


def test_bolt_cutout_is_scaled_and_written_with_the_pl_outline(
    tmp_path: Path,
) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "bolt-cutout.dxf"
    _save_geometry_source(drawing, include_hole=True)

    batch = compiler.split_pl(drawing, tmp_path / "results")

    assert batch.success_count == 1
    saved = ezdxf.readfile(tmp_path / "results" / "q6-b-62.dxf")
    plate_cut = tuple(saved.modelspace().query('*[layer=="PLATE_CUT"]'))
    assert len(geometry._proved_components(plate_cut)) == 2


def test_two_equally_credible_main_views_are_rejected(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "two-main-views.dxf"
    _save_geometry_source(drawing, duplicate_main=True)
    context, metadata = _load_geometry_context(drawing)

    with pytest.raises(PLSplitError) as error:
        geometry.analyze_geometry(context, metadata)

    assert error.value.code == "MAIN_VIEW_AMBIGUOUS"


@pytest.mark.parametrize(
    ("height", "accepted"),
    ((501.0000000003, True), (501.000002, False)),
)
def test_width_tolerance_accepts_only_a_floating_point_tail(
    tmp_path: Path,
    height: float,
    accepted: bool,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "width-floating-tail.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="width-floating-tail",
        specification="PL25*500",
        bom_length="225",
        y=-900.0,
    )
    _add_closed_polygon(
        layout,
        ((0.0, 0.0), (225.0, 0.0), (225.0, height), (0.0, height)),
    )
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    if accepted:
        outline, section = geometry.analyze_geometry(context, metadata)
        assert section is None
        assert outline.width_mm == pytest.approx(height)
    else:
        with pytest.raises(PLSplitError) as error:
            geometry.analyze_geometry(context, metadata)
        assert error.value.code == "MAIN_VIEW_MISSING"


def test_bom_length_disambiguates_a_vertical_end_view(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "vertical-end-view.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="vertical-end-view",
        specification="PL10*150",
        bom_length="645",
        y=-900.0,
    )
    _add_closed_polygon(
        layout,
        ((0.0, 0.0), (645.0, 0.0), (645.0, 150.0), (0.0, 150.0)),
    )
    _add_closed_polygon(
        layout,
        ((0.0, -500.0), (645.0, -500.0), (645.0, -490.0), (0.0, -490.0)),
    )
    _add_closed_polygon(
        layout,
        ((800.0, 0.0), (810.0, 0.0), (810.0, 150.0), (800.0, 150.0)),
    )
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    outline, section = geometry.analyze_geometry(context, metadata)

    assert outline.projection_length_mm == pytest.approx(645.0)
    assert section is not None
    assert section.k_length_mm == pytest.approx(645.0)


@pytest.mark.parametrize(
    ("delta", "accepted"),
    ((0.00005, True), (0.0001, False)),
)
def test_main_boundary_area_noise_has_a_narrow_tolerance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delta: float,
    accepted: bool,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "tiny-boundary-area-noise.dxf"
    _save_geometry_source(drawing, include_section=False)
    context, metadata = _load_geometry_context(drawing)
    original_outer_entities = geometry._outer_entities

    def contracted_outer_entities(component):
        entities = list(original_outer_entities(component))
        max_x = component.polygon.bounds[2]
        for entity in entities:
            if entity.dxftype() != "LINE":
                continue
            for attribute in ("start", "end"):
                point = entity.dxf.get(attribute)
                if abs(float(point.x) - max_x) <= 1e-9:
                    entity.dxf.set(attribute, (float(point.x) - delta, float(point.y)))
        return tuple(entities)

    monkeypatch.setattr(geometry, "_outer_entities", contracted_outer_entities)

    if accepted:
        outline, section = geometry.analyze_geometry(context, metadata)
        assert section is None
        assert outline.projection_length_mm == pytest.approx(399.0 - delta)
    else:
        with pytest.raises(PLSplitError) as error:
            geometry.analyze_geometry(context, metadata)
        assert error.value.code == "MAIN_BOUNDARY_MISMATCH"


def test_output_station_proof_uses_only_the_topology_tolerance() -> None:
    writer = importlib.import_module("steel_dxf_split_pl.writer")
    document = _new_source_document()
    _add_closed_polygon(
        document.modelspace(),
        ((0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)),
    )
    entities = tuple(document.modelspace().query('*[layer=="Part"]'))
    accepted = ((5.0, 0.068),)
    topology = writer._boundary_topology(entities, accepted)

    assert writer._station_nodes(topology, accepted)

    rejected = ((5.0, 0.100001),)
    topology = writer._boundary_topology(entities, rejected)
    with pytest.raises(PLSplitError) as error:
        writer._station_nodes(topology, rejected)

    assert error.value.code == "OUTPUT_INTERVAL_CONTRACT"


def test_missing_closed_section_is_an_ordinary_flat_plate(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "missing-section.dxf"
    _save_geometry_source(drawing, include_section=False)
    context, metadata = _load_geometry_context(drawing)

    outline, section = geometry.analyze_geometry(context, metadata)

    assert section is None
    assert outline.projection_length_mm == pytest.approx(399.0)
    assert outline.width_mm == pytest.approx(300.0)


def test_unmatched_independent_closed_view_is_not_assumed_to_be_flat(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "unmatched-view.dxf"
    _save_geometry_source(drawing, include_section=False)
    document = ezdxf.readfile(drawing)
    _add_closed_polygon(
        document.modelspace(),
        ((0.0, -500.0), (300.0, -500.0), (300.0, -475.0), (0.0, -475.0)),
    )
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    with pytest.raises(PLSplitError) as error:
        geometry.analyze_geometry(context, metadata)

    assert error.value.code == "SECTION_MISSING"


def test_flat_plate_uses_projection_and_bom_without_k_factor(tmp_path: Path) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    drawing = tmp_path / "flat.dxf"
    output = tmp_path / "flat-output"
    _save_geometry_source(drawing, include_section=False)

    batch = compiler.split_pl(drawing, output)
    report = json.loads(batch.report_path.read_text(encoding="utf-8"))

    assert batch.success_count == 1
    developed = batch.items[0].compilation.developed
    assert developed.section is None
    assert developed.metrics.k_factor is None
    assert developed.metrics.k_length_mm == pytest.approx(399.0)
    assert developed.metrics.raw_length_mm == pytest.approx(470.0)
    assert developed.metrics.target_length_mm == pytest.approx(470.0)
    assert developed.longitudinal.selection_reason == "uniform_projection_fallback"
    item = report["items"][0]
    assert item["evidence"]["plate_mode"] == "flat"
    assert item["lengths"]["k_factor"] is None
    assert item["lengths"]["k_length_mm"] is None
    assert item["lengths"]["target_mm"] == pytest.approx(470.0)


def test_flat_plate_completes_two_short_tip_edges_like_professional_result(
    tmp_path: Path,
) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    drawing = tmp_path / "FJ-F3-cb-121.dxf"
    output = tmp_path / "output"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="FJ-F3-cb-121",
        specification="PL30*675",
        bom_length="1399",
        y=-900.0,
    )
    points = (
        (0.0, 112.5),
        (0.0, 562.5),
        (450.0, 675.0),
        (1249.266525, 675.0),
        (967.059808, 392.792330),
        (984.737507, 375.114690),
        (1004.535771, 394.913021),
        (1399.448791, 0.0),
        (450.0, 0.0),
    )
    _add_closed_polygon(layout, points)
    layout.add_line(
        (970.594822, 389.255984),
        (984.737507, 375.114690),
        dxfattribs={"layer": "Part"},
    )
    document.saveas(drawing)

    batch = compiler.split_pl(drawing, output)

    assert batch.success_count == 1
    saved = ezdxf.readfile(output / "FJ-F3-cb-121.dxf")
    cuts = tuple(saved.modelspace().query('LINE[layer=="PLATE_CUT"]'))
    assert len(cuts) == 7
    raw_vertices = {
        (float(point.x), float(point.y))
        for entity in cuts
        for point in (entity.dxf.start, entity.dxf.end)
    }
    min_x = min(point[0] for point in raw_vertices)
    min_y = min(point[1] for point in raw_vertices)
    vertices = {
        (round(x - min_x, 3), round(y - min_y, 3))
        for x, y in raw_vertices
    }
    assert len(vertices) == 7
    assert (986.894, 412.591) in vertices
    assert max(point[0] for point in vertices) == pytest.approx(1399.5)
    assert max(point[1] for point in vertices) == pytest.approx(675.0)


def test_flat_plate_tip_completion_prefers_the_proved_outer_source_face(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "FJ-F3-cb-104.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="FJ-F3-cb-104",
        specification="PL30*600",
        bom_length="1728",
        y=-900.0,
    )
    segments = (
        ((1328.284285004586, 0.0), (0.0, 0.000004604648)),
        ((341.199369143208, 348.273289697860), (331.818394635513, 338.892315164420)),
        ((18.762026177937, 0.000003718422), (354.115775360726, 335.356523610514)),
        ((1728.284299304287, 100.000003807109), (1328.284285004586, 0.0)),
        ((1728.284299301202, 500.000003806133), (1328.284284999943, 600.000007604976)),
        ((1328.284284999943, 600.000007604976), (70.710706819110, 600.000002975452)),
        ((89.472810498148, 600.000003866706), (341.199369143208, 348.273289697860)),
        ((341.199369143208, 348.273289697860), (354.115775360726, 335.356523610514)),
        ((331.818394635513, 338.892315164420), (70.710706819110, 600.000002975452)),
        ((1728.284299301202, 500.000003806133), (1728.284299304287, 100.000003807109)),
        ((344.734684322553, 344.737537142238), (0.0, 0.000004604648)),
        ((354.115775360726, 335.356523610514), (341.199369143208, 348.273289697860)),
    )
    for start, end in segments:
        layout.add_line(start, end, dxfattribs={"layer": "Part"})
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    outline, section = geometry.analyze_geometry(context, metadata)

    assert section is None
    assert len(outline.outer_entities) == 7
    assert outline.projection_length_mm == pytest.approx(1728.284, abs=0.001)
    raw_vertices = {
        (float(point.x), float(point.y))
        for entity in outline.outer_entities
        for point in (entity.dxf.start, entity.dxf.end)
    }
    min_x = min(point[0] for point in raw_vertices)
    min_y = min(point[1] for point in raw_vertices)
    vertices = {(x - min_x, y - min_y) for x, y in raw_vertices}
    expected = {
        (0.0, 0.0),
        (70.711, 600.0),
        (335.35, 335.36),
        (1328.284, 0.0),
        (1328.284, 600.0),
        (1728.284, 100.0),
        (1728.284, 500.0),
    }
    assert all(
        min(
            ((actual[0] - wanted[0]) ** 2 + (actual[1] - wanted[1]) ** 2) ** 0.5
            for actual in vertices
        )
        <= 0.02
        for wanted in expected
    )


def test_flat_plate_tip_completion_keeps_normal_v_notch_and_collinear_segments(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "normal-v-notch.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="normal-v-notch",
        specification="PL10*500",
        bom_length="1000",
        y=-900.0,
    )
    points = (
        (0.0, 0.0),
        (500.0, 0.0),
        (1000.0, 0.0),
        (1000.0, 500.0),
        (800.0, 500.0),
        (650.0, 350.0),
        (800.0, 200.0),
        (0.0, 200.0),
    )
    _add_closed_polygon(layout, points)
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    outline, section = geometry.analyze_geometry(context, metadata)

    assert section is None
    assert len(outline.outer_entities) == len(points)
    endpoints = {
        (round(float(entity.dxf.start.x), 3), round(float(entity.dxf.start.y), 3))
        for entity in outline.outer_entities
    }
    assert endpoints == set(points)


def test_flat_plate_recovers_fragmented_round_corner_as_one_native_arc(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "flat-rounded-corner.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="flat-rounded-corner",
        specification="PL25*100",
        bom_length="200",
        y=-900.0,
    )
    layout.add_line((0.0, 0.0), (155.0, 0.0), dxfattribs={"layer": "Part"})
    layout.add_line(
        (155.0, 0.0),
        (168.905764747, 2.202456766),
        dxfattribs={"layer": "Part"},
    )
    layout.add_arc(
        (155.0, 45.0),
        45.0,
        -72.0,
        -18.0,
        dxfattribs={"layer": "Part"},
    )
    layout.add_line(
        (197.797543234, 31.094235253),
        (200.0, 45.0),
        dxfattribs={"layer": "Part"},
    )
    layout.add_line((200.0, 45.0), (200.0, 100.0), dxfattribs={"layer": "Part"})
    layout.add_line((200.0, 100.0), (0.0, 100.0), dxfattribs={"layer": "Part"})
    layout.add_line((0.0, 100.0), (0.0, 0.0), dxfattribs={"layer": "Part"})
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    outline, section = geometry.analyze_geometry(context, metadata)

    assert section is None
    assert [entity.dxftype() for entity in outline.outer_entities].count("ARC") == 1
    assert len(outline.outer_entities) == 5
    arc = next(entity for entity in outline.outer_entities if entity.dxftype() == "ARC")
    assert float(arc.dxf.radius) == pytest.approx(45.0)
    assert float(arc.dxf.start_angle) % 360.0 == pytest.approx(270.0)
    assert float(arc.dxf.end_angle) % 360.0 == pytest.approx(0.0)
    assert geometry.validate_closed_outline(outline.outer_entities).is_valid


def test_flat_plate_recovers_a_fragmented_round_notch_without_extra_nodes(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "flat-round-notch.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="flat-round-notch",
        specification="PL10*50",
        bom_length="100",
        y=-900.0,
    )
    layout.add_line((0.0, 0.0), (0.0, 50.0), dxfattribs={"layer": "Part"})
    layout.add_line((0.0, 50.0), (100.0, 50.0), dxfattribs={"layer": "Part"})
    layout.add_line((100.0, 50.0), (100.0, 0.0), dxfattribs={"layer": "Part"})
    layout.add_line((100.0, 0.0), (68.0, 0.0), dxfattribs={"layer": "Part"})
    layout.add_line((68.0, 0.0), (68.0, 1.0), dxfattribs={"layer": "Part"})
    layout.add_line(
        (68.0, 1.0),
        (67.548702419, 5.005376811),
        dxfattribs={"layer": "Part"},
    )
    layout.add_arc(
        (50.0, 1.0),
        18.0,
        12.857142857,
        77.142857143,
        dxfattribs={"layer": "Part"},
    )
    layout.add_line(
        (54.005376811, 18.548702419),
        (50.0, 19.0),
        dxfattribs={"layer": "Part"},
    )
    layout.add_line(
        (50.0, 19.0),
        (45.994623189, 18.548702419),
        dxfattribs={"layer": "Part"},
    )
    layout.add_arc(
        (50.0, 1.0),
        18.0,
        102.857142857,
        167.142857143,
        dxfattribs={"layer": "Part"},
    )
    layout.add_line(
        (32.451297581, 5.005376811),
        (32.0, 1.0),
        dxfattribs={"layer": "Part"},
    )
    layout.add_line((32.0, 1.0), (32.0, 0.0), dxfattribs={"layer": "Part"})
    layout.add_line((32.0, 0.0), (0.0, 0.0), dxfattribs={"layer": "Part"})
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    outline, section = geometry.analyze_geometry(context, metadata)

    assert section is None
    assert len(outline.outer_entities) == 8
    [arc] = [entity for entity in outline.outer_entities if entity.dxftype() == "ARC"]
    assert float(arc.dxf.radius) == pytest.approx(18.0)
    assert float(arc.dxf.start_angle) == pytest.approx(12.857142857)
    assert float(arc.dxf.end_angle) == pytest.approx(167.142857143)
    assert geometry.validate_closed_outline(outline.outer_entities).is_valid


def test_non_x_main_axis_is_rejected(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "vertical-main.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="470",
        y=-900.0,
    )
    _add_closed_polygon(layout, ((0.0, 0.0), (300.0, 0.0), (300.0, 399.0), (0.0, 399.0)))
    _add_closed_polygon(
        layout,
        ((0.0, -500.0), (300.0, -500.0), (300.0, -475.0), (0.0, -475.0)),
    )
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    with pytest.raises(PLSplitError) as error:
        geometry.analyze_geometry(context, metadata)

    assert error.value.code == "MAIN_VIEW_MISSING"


def test_x_axis_main_can_be_shorter_than_width_and_use_nominal_width_rounding(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "short-wide-main.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="anonymous-short-wide",
        specification="PL25*500",
        bom_length="225",
        y=-900.0,
    )
    _add_closed_polygon(
        layout,
        ((0.0, 0.0), (225.0, 0.0), (225.0, 500.8), (0.0, 500.8)),
    )
    _add_closed_polygon(
        layout,
        ((0.0, -500.0), (225.0, -500.0), (225.0, -475.0), (0.0, -475.0)),
    )
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    outline, section = geometry.analyze_geometry(context, metadata)

    assert outline.projection_length_mm == pytest.approx(225.0)
    assert outline.width_mm == pytest.approx(500.8)
    assert section.k_length_mm == pytest.approx(225.0)


def test_translated_equivalent_sections_are_one_geometric_proof(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "equivalent-sections.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="anonymous-equivalent-sections",
        specification="PL25*300",
        bom_length="600",
        y=-900.0,
    )
    _add_closed_polygon(
        layout,
        ((0.0, 0.0), (600.0, 0.0), (600.0, 300.0), (0.0, 300.0)),
    )
    for y in (-500.0, -700.0):
        _add_closed_polygon(
            layout,
            ((0.0, y), (600.0, y), (600.0, y + 25.0), (0.0, y + 25.0)),
        )
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    _, section = geometry.analyze_geometry(context, metadata)

    assert section.k_length_mm == pytest.approx(600.0)
    assert section.candidate_count == 2


def test_sub_tolerance_boundary_noise_is_not_emitted_as_a_zero_length_cut(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    drawing = tmp_path / "tiny-boundary-edge.dxf"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="470",
        y=-900.0,
    )
    _add_closed_polygon(
        layout,
        (
            (0.0, 0.0),
            (100.0, 0.0),
            (100.00001, 0.0),
            (399.0, 0.0),
            (399.0, 300.0),
            (0.0, 300.0),
        ),
    )
    _add_closed_polygon(
        layout,
        ((0.0, -500.0), (399.0, -500.0), (399.0, -475.0), (0.0, -475.0)),
    )
    document.saveas(drawing)
    context, metadata = _load_geometry_context(drawing)

    outline, _ = geometry.analyze_geometry(context, metadata)

    assert all(geometry.native_entity_length(entity) > 0.001 for entity in outline.outer_entities)


def _save_curved_geometry_source(path: Path) -> None:
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="q6-b-62",
        specification="PL25*300",
        bom_length="600",
        y=-900.0,
    )
    layout.add_line((0.0, 0.0), (400.0, 0.0), dxfattribs={"layer": "Part"})
    layout.add_arc(
        (400.0, 150.0),
        150.0,
        270.0,
        90.0,
        dxfattribs={"layer": "Part"},
    )
    layout.add_line((400.0, 300.0), (0.0, 300.0), dxfattribs={"layer": "Part"})
    layout.add_line((0.0, 300.0), (0.0, 0.0), dxfattribs={"layer": "Part"})
    _add_closed_polygon(
        layout,
        ((0.0, -500.0), (550.0, -500.0), (550.0, -475.0), (0.0, -475.0)),
    )
    document.saveas(path)


def _developed_plate(path: Path):
    contracts = importlib.import_module("steel_dxf_split_pl.contracts")
    geometry = importlib.import_module("steel_dxf_split_pl.geometry")
    development = importlib.import_module("steel_dxf_split_pl.development")
    longitudinal = importlib.import_module("steel_dxf_split_pl.longitudinal")
    context, metadata = _load_geometry_context(path)
    outline, section = geometry.analyze_geometry(context, metadata)
    longitudinal_proof = longitudinal.analyze_longitudinal_outline(
        outline.outer_entities,
        outline.polygon,
        thickness_mm=metadata.thickness_mm,
    )
    entities, metrics = development.transform_outline(
        outline.outer_entities,
        longitudinal=longitudinal_proof,
        projection_length_mm=outline.projection_length_mm,
        k_length_mm=section.k_length_mm,
        bom_length_mm=metadata.bom_length_mm,
        anchor_x_mm=outline.anchor_x_mm,
    )
    return contracts.DevelopedPlate(
        metadata=metadata,
        outline=outline,
        section=section,
        longitudinal=longitudinal_proof,
        transformed_entities=entities,
        metrics=metrics,
    )


def test_writer_emits_clean_r2007_mm_layers_label_and_native_downstream_arcs(
    tmp_path: Path,
) -> None:
    writer = importlib.import_module("steel_dxf_split_pl.writer")
    source = tmp_path / "curved.dxf"
    output = tmp_path / "q6-b-62.dxf"
    _save_curved_geometry_source(source)
    developed = _developed_plate(source)

    result = writer.write_pl_dxf(developed, output)
    saved = ezdxf.readfile(output)

    assert saved.dxfversion == "AC1021"
    assert saved.header["$INSUNITS"] == 4
    assert {entity.dxf.layer for entity in saved.modelspace()} == {
        "PLATE_CUT",
        "PART_LABEL",
    }
    assert len(saved.modelspace().query('ARC[layer=="PLATE_CUT"]')) >= 1
    assert len(saved.modelspace().query('ELLIPSE[layer=="PLATE_CUT"]')) == 0
    labels = list(saved.modelspace().query('TEXT[layer=="PART_LABEL"]'))
    assert [label.dxf.text for label in labels] == ["p=q6-b-62"]
    assert labels[0].dxf.height == pytest.approx(30.0)
    assert labels[0].dxf.style == "SplitChinese"
    assert saved.audit().has_errors is False
    assert result.min_x_mm == pytest.approx(0.0, abs=0.001)
    assert result.length_mm == pytest.approx(600.0, abs=0.001)
    assert result.width_mm == pytest.approx(300.0, abs=0.001)
    assert result.label == "p=q6-b-62"


def test_saved_output_validation_rejects_non_manufacturing_entities(
    tmp_path: Path,
) -> None:
    writer = importlib.import_module("steel_dxf_split_pl.writer")
    source = tmp_path / "curved.dxf"
    output = tmp_path / "q6-b-62.dxf"
    _save_curved_geometry_source(source)
    developed = _developed_plate(source)
    writer.write_pl_dxf(developed, output)
    altered = ezdxf.readfile(output)
    altered.modelspace().add_circle(
        (10.0, 10.0),
        1.0,
        dxfattribs={"layer": "Z-DIMENSIONS"},
    )
    altered.saveas(output)

    with pytest.raises(PLSplitError) as error:
        writer.validate_saved_pl_dxf(output, developed)

    assert error.value.code == "OUTPUT_ENTITY_CONTRACT"


def test_writer_shrinks_a_pl_label_that_cannot_retain_thirty_mm(
    tmp_path: Path,
) -> None:
    writer = importlib.import_module("steel_dxf_split_pl.writer")
    source = tmp_path / "curved.dxf"
    output = tmp_path / "long-label.dxf"
    _save_curved_geometry_source(source)
    developed = _developed_plate(source)
    developed = replace(
        developed,
        metadata=replace(
            developed.metadata,
            part_number="part-number-that-cannot-fit-at-thirty-millimeters",
        ),
    )

    writer.write_pl_dxf(developed, output)

    saved = ezdxf.readfile(output)
    labels = list(saved.modelspace().query('TEXT[layer=="PART_LABEL"]'))
    assert len(labels) == 1
    assert labels[0].dxf.text == "p=part-number-that-cannot-fit-at-thirty-millimeters"
    assert labels[0].dxf.height < 30.0


def test_writer_uses_a_smaller_label_for_a_small_flat_plate(tmp_path: Path) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    source = tmp_path / "small-flat.dxf"
    output = tmp_path / "small-flat-output"
    document = _new_source_document()
    layout = document.modelspace()
    _add_metadata(
        layout,
        part_number="GJ-B1-s-4",
        specification="PL10*50",
        bom_length="50",
        y=-900.0,
    )
    _add_closed_polygon(
        layout,
        ((0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 50.0)),
    )
    layout.add_circle((25.0, 25.0), 13.0, dxfattribs={"layer": "Bolt"})
    document.saveas(source)

    batch = compiler.split_pl(source, output)

    assert batch.success_count == 1
    saved = ezdxf.readfile(output / "GJ-B1-s-4.dxf")
    [label] = saved.modelspace().query('TEXT[layer=="PART_LABEL"]')
    assert 5.0 <= float(label.dxf.height) <= 8.0


def _add_sheet_geometry(
    layout,
    *,
    part_number: str,
    thickness_mm: float,
    width_mm: float,
    bom_length_mm: float,
    include_section: bool = True,
) -> None:
    _add_metadata(
        layout,
        part_number=part_number,
        specification=f"PL{thickness_mm:g}*{width_mm:g}",
        bom_length=f"{bom_length_mm:g}",
        y=-900.0,
    )
    _add_closed_polygon(
        layout,
        ((0.0, 0.0), (399.0, 0.0), (399.0, width_mm), (0.0, width_mm)),
    )
    layout.add_line(
        (100.0, 0.0),
        (100.0, width_mm),
        dxfattribs={"layer": "Part"},
    )
    if include_section:
        _add_closed_polygon(
            layout,
            (
                (0.0, -500.0),
                (399.0, -500.0),
                (399.0, -500.0 + thickness_mm),
                (0.0, -500.0 + thickness_mm),
            ),
        )


def _save_combined_geometry_dxf(
    path: Path,
    *,
    reject_second: bool = False,
) -> None:
    document = _new_source_document()
    first = document.blocks.new("sheet-a")
    _add_sheet_geometry(
        first,
        part_number="q6-b-62",
        thickness_mm=25.0,
        width_mm=300.0,
        bom_length_mm=470.0,
    )
    second = document.blocks.new("sheet-b")
    _add_sheet_geometry(
        second,
        part_number="p=q6-b-71",
        thickness_mm=30.0,
        width_mm=350.0,
        bom_length_mm=480.0,
        include_section=True,
    )
    if reject_second:
        _add_closed_polygon(
            second,
            ((0.0, 500.0), (399.0, 500.0), (399.0, 850.0), (0.0, 850.0)),
        )
    document.modelspace().add_blockref("sheet-a", (0.0, 0.0))
    document.modelspace().add_blockref("sheet-b", (1000.0, 0.0))
    document.saveas(path)


def test_batch_split_publishes_one_dxf_per_part_and_complete_json_report(
    tmp_path: Path,
) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    source = tmp_path / "combined.dxf"
    output = tmp_path / "output"
    _save_combined_geometry_dxf(source)

    batch = compiler.split_pl(source, output)
    report = json.loads(batch.report_path.read_text(encoding="utf-8"))

    assert batch.exit_code == 0
    assert batch.success_count == 2
    assert batch.rejected_count == 0
    assert {path.name for path in output.glob("*.dxf")} == {
        "q6-b-62.dxf",
        "q6-b-71.dxf",
    }
    assert report["schema"] == "steel-dxf-split-pl-report/2"
    assert [item["part_number"] for item in report["items"]] == [
        "q6-b-62",
        "q6-b-71",
    ]
    assert {item["status"] for item in report["items"]} == {"success"}
    assert report["items"][0]["lengths"]["target_mm"] == pytest.approx(470.0)
    assert report["items"][0]["lengths"]["total_extension_mm"] == pytest.approx(71.0)
    assert report["items"][0]["geometry"]["source_width_mm"] == pytest.approx(300.0)
    assert report["items"][0]["geometry"]["source_anchor_x_mm"] == pytest.approx(0.0)
    transform = report["items"][0]["transform"]
    assert set(transform) == {
        "carrier_interval_indices",
        "selection_reason",
        "total_extension_mm",
        "carrier_upper_scale_x",
        "carrier_lower_scale_x",
        "intervals",
    }
    assert transform["carrier_interval_indices"] == [0]
    assert transform["selection_reason"] == "unique_longest_body"
    assert transform["total_extension_mm"] == pytest.approx(71.0)
    assert transform["carrier_upper_scale_x"] == pytest.approx(470.0 / 399.0)
    assert transform["carrier_lower_scale_x"] == pytest.approx(470.0 / 399.0)
    assert sum(interval["is_carrier"] for interval in transform["intervals"]) == 1
    assert transform["intervals"] == [
        {
            "index": 0,
            "source_upper_span_mm": pytest.approx(399.0),
            "source_lower_span_mm": pytest.approx(399.0),
            "output_upper_span_mm": pytest.approx(470.0),
            "output_lower_span_mm": pytest.approx(470.0),
            "downstream_shift_mm": pytest.approx(0.0),
            "is_carrier": True,
        }
    ]
    assert "scale_x" not in transform
    assert report["items"][0]["output"]["label"] == "p=q6-b-62"
    assert report["items"][0]["output"]["width_mm"] == pytest.approx(
        report["items"][0]["geometry"]["source_width_mm"]
    )


def test_rejected_sheet_does_not_prevent_valid_sheet_publication(tmp_path: Path) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    source = tmp_path / "partial.dxf"
    output = tmp_path / "output"
    _save_combined_geometry_dxf(source, reject_second=True)

    batch = compiler.split_pl(source, output)

    assert batch.exit_code == 1
    assert batch.success_count == 1
    assert batch.rejected_count == 1
    assert (output / "q6-b-62.dxf").is_file()
    assert not (output / "q6-b-71.dxf").exists()
    rejected = next(item for item in batch.items if item.status == "rejected")
    assert rejected.part_number == "q6-b-71"
    assert rejected.error_code == "MAIN_VIEW_AMBIGUOUS"


def test_existing_part_output_is_preserved_without_overwrite(tmp_path: Path) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    source = tmp_path / "combined.dxf"
    output = tmp_path / "output"
    output.mkdir()
    sentinel = output / "q6-b-62.dxf"
    sentinel.write_bytes(b"keep-existing")
    _save_combined_geometry_dxf(source)

    batch = compiler.split_pl(source, output)

    assert batch.exit_code == 1
    assert sentinel.read_bytes() == b"keep-existing"
    first = next(item for item in batch.items if item.part_number == "q6-b-62")
    assert first.status == "rejected"
    assert first.error_code == "OUTPUT_EXISTS"
    assert (output / "q6-b-71.dxf").is_file()


def test_overwrite_replaces_only_the_exact_owned_part_target(tmp_path: Path) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    source = tmp_path / "combined.dxf"
    output = tmp_path / "output"
    output.mkdir()
    target = output / "q6-b-62.dxf"
    neighbor = output / "unrelated.dxf"
    target.write_bytes(b"replace-me")
    neighbor.write_bytes(b"preserve-me")
    _save_combined_geometry_dxf(source)

    batch = compiler.split_pl(source, output, overwrite=True)

    assert batch.exit_code == 0
    assert ezdxf.readfile(target).audit().has_errors is False
    assert neighbor.read_bytes() == b"preserve-me"


def test_duplicate_part_numbers_are_rejected_before_any_target_is_published(
    tmp_path: Path,
) -> None:
    compiler = importlib.import_module("steel_dxf_split_pl.compiler")
    source = tmp_path / "duplicate.dxf"
    output = tmp_path / "output"
    document = _new_source_document()
    for block_name in ("sheet-a", "sheet-b"):
        sheet = document.blocks.new(block_name)
        _add_sheet_geometry(
            sheet,
            part_number="q6-b-62",
            thickness_mm=25.0,
            width_mm=300.0,
            bom_length_mm=470.0,
        )
        document.modelspace().add_blockref(block_name, (0.0, 0.0))
    document.saveas(source)

    batch = compiler.split_pl(source, output)

    assert batch.exit_code == 1
    assert batch.success_count == 0
    assert batch.rejected_count == 2
    assert {item.error_code for item in batch.items} == {"DUPLICATE_PART_NUMBER"}
    assert not list(output.glob("*.dxf"))


def test_cli_returns_zero_one_and_two_with_json_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli = importlib.import_module("steel_dxf_split_pl.cli")
    valid = tmp_path / "valid.dxf"
    partial = tmp_path / "partial.dxf"
    invalid = tmp_path / "invalid.dwg"
    _save_combined_geometry_dxf(valid)
    _save_combined_geometry_dxf(partial, reject_second=True)
    invalid.write_bytes(b"not-a-dwg")

    assert cli.main([str(valid), "--output-dir", str(tmp_path / "success")]) == 0
    success_payload = json.loads(capsys.readouterr().out)
    assert success_payload["success_count"] == 2
    assert cli.main([str(partial), "--output-dir", str(tmp_path / "partial-out")]) == 1
    partial_payload = json.loads(capsys.readouterr().out)
    assert partial_payload["rejected_count"] == 1
    assert cli.main([str(invalid), "--output-dir", str(tmp_path / "fatal")]) == 2
    fatal_payload = json.loads(capsys.readouterr().out)
    assert fatal_payload["status"] == "fatal"
    assert fatal_payload["error"]["code"] == "DWG_NOT_SUPPORTED"


def test_pl_runtime_does_not_load_bh_box_pipeline_or_merge_modules(
    tmp_path: Path,
) -> None:
    source = tmp_path / "combined.dxf"
    output = tmp_path / "output"
    _save_combined_geometry_dxf(source)
    script = "\n".join(
        (
            "import json, sys",
            "from pathlib import Path",
            "from steel_dxf_split_pl import split_pl",
            f"result = split_pl(Path({str(source)!r}), Path({str(output)!r}))",
            "forbidden = sorted(name for name in sys.modules if name == 'steel_dxf_split.pipeline' or name.startswith('steel_dxf_split.box') or name.startswith('steel_dxf_split.bh_') or name == 'tools.merge_sheet')",
            "print(json.dumps({'exit_code': result.exit_code, 'forbidden': forbidden}))",
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload == {"exit_code": 0, "forbidden": []}


def test_independent_stage_launcher_executes_pl_without_bh_box_entrypoint(
    tmp_path: Path,
) -> None:
    source = tmp_path / "combined.dxf"
    output = tmp_path / "output"
    _save_combined_geometry_dxf(source)
    launcher_source = REPO_ROOT / "Stages" / "steel_dxf_split_pl" / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(launcher_source), environment.get("PYTHONPATH")))
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "steel_dxf_split_pl.cli",
            str(source),
            "--output-dir",
            str(output),
        ],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["success_count"] == 2
    assert payload["rejected_count"] == 0
    assert sorted(path.name for path in output.glob("*.dxf")) == [
        "q6-b-62.dxf",
        "q6-b-71.dxf",
    ]


def test_independent_stage_owns_pl_runtime_without_unified_split_package(
    tmp_path: Path,
) -> None:
    source = tmp_path / "combined.dxf"
    output = tmp_path / "output"
    _save_combined_geometry_dxf(source)
    launcher_source = REPO_ROOT / "Stages" / "steel_dxf_split_pl" / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(launcher_source), environment.get("PYTHONPATH")))
    )
    script = "\n".join(
        (
            "import json, sys",
            "from contextlib import redirect_stdout",
            "from io import StringIO",
            "from steel_dxf_split_pl.cli import main",
            "stdout = StringIO()",
            f"with redirect_stdout(stdout): exit_code = main([{str(source)!r}, '--output-dir', {str(output)!r}])",
            "forbidden = sorted(name for name in sys.modules if name == 'steel_dxf_split' or name.startswith('steel_dxf_split.'))",
            "print(json.dumps({'exit_code': exit_code, 'forbidden': forbidden}))",
        )
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload == {"exit_code": 0, "forbidden": []}
