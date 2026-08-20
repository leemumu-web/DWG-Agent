from __future__ import annotations

from decimal import Decimal
import importlib
from pathlib import Path

import ezdxf
import pytest
from steel_dxf_split.pl.contracts import PLSplitError


def test_k_half_neutral_axis_uses_the_mean_of_both_plate_faces() -> None:
    development = importlib.import_module("steel_dxf_split.pl.development")

    assert development.neutral_axis_length((470.0, 472.0)) == pytest.approx(471.0)


def test_development_uses_the_largest_of_projection_k_and_bom_lengths() -> None:
    development = importlib.import_module("steel_dxf_split.pl.development")

    metrics = development.calculate_development(
        projection_length_mm=399.0,
        surface_lengths_mm=(468.0, 472.0),
        bom_length_mm=469.4,
        anchor_x_mm=12.0,
    )

    assert metrics.k_factor == 0.5
    assert metrics.k_length_mm == pytest.approx(470.0)
    assert metrics.raw_length_mm == pytest.approx(470.0)
    assert metrics.target_length_mm == pytest.approx(470.0)
    assert metrics.scale_x == pytest.approx(470.0 / 399.0)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Decimal("10.0"), Decimal("10.0")),
        (Decimal("10.0000004"), Decimal("10.0")),
        (Decimal("10.000002"), Decimal("10.1")),
        (Decimal("10.099999"), Decimal("10.1")),
        (Decimal("10.1000011"), Decimal("10.2")),
    ],
)
def test_length_is_ceiled_to_one_decimal_without_arbitrary_allowance(
    source: Decimal,
    expected: Decimal,
) -> None:
    development = importlib.import_module("steel_dxf_split.pl.development")

    assert development.ceil_tenth_mm(source) == expected


def test_global_x_transform_anchors_left_edge_preserves_y_and_converts_arc() -> None:
    development = importlib.import_module("steel_dxf_split.pl.development")
    document = ezdxf.new("R2007")
    line = document.modelspace().add_line((10.0, 2.0), (20.0, 2.0))
    arc = document.modelspace().add_arc((20.0, 7.0), 5.0, 270.0, 90.0)

    transformed, metrics = development.transform_outline(
        (line, arc),
        projection_length_mm=20.0,
        surface_lengths_mm=(25.0, 25.0),
        bom_length_mm=20.0,
        anchor_x_mm=10.0,
    )

    assert transformed[0].dxftype() == "LINE"
    assert transformed[0].dxf.start.x == pytest.approx(10.0)
    assert transformed[0].dxf.start.y == pytest.approx(2.0)
    assert transformed[0].dxf.end.x == pytest.approx(22.5)
    assert transformed[1].dxftype() == "ELLIPSE"
    assert metrics.scale_x == pytest.approx(1.25)


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
    layout.add_text(specification, dxfattribs={"layer": "OtherObjectType", "height": 10}).dxf.insert = (
        100.0,
        y,
    )
    layout.add_text(bom_length, dxfattribs={"layer": "OtherObjectType", "height": 10}).dxf.insert = (
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
    source = importlib.import_module("steel_dxf_split.pl.source")
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
    source = importlib.import_module("steel_dxf_split.pl.source")

    assert source.canonical_part_number("p=q6-b-62") == "q6-b-62"
    assert source.canonical_part_number("Q6-B-62") == "Q6-B-62"


def test_conflicting_part_marks_are_rejected_instead_of_using_the_filename(
    tmp_path: Path,
) -> None:
    source = importlib.import_module("steel_dxf_split.pl.source")
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


def test_multiple_pl_rows_are_rejected(tmp_path: Path) -> None:
    source = importlib.import_module("steel_dxf_split.pl.source")
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
    source = importlib.import_module("steel_dxf_split.pl.source")
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
    source = importlib.import_module("steel_dxf_split.pl.source")
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
    source = importlib.import_module("steel_dxf_split.pl.source")
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
    source = importlib.import_module("steel_dxf_split.pl.source")
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
        _add_closed_polygon(
            layout,
            ((100.0, 100.0), (120.0, 100.0), (120.0, 120.0), (100.0, 120.0)),
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
    source = importlib.import_module("steel_dxf_split.pl.source")
    [context] = source.load_source_contexts(path)
    return context, source.extract_metadata(context)


def test_geometry_proof_selects_only_the_main_outer_boundary_and_unique_section(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
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
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
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


def test_main_view_hole_is_rejected_until_hole_development_is_defined(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
    drawing = tmp_path / "hole.dxf"
    _save_geometry_source(drawing, include_hole=True)
    context, metadata = _load_geometry_context(drawing)

    with pytest.raises(PLSplitError) as error:
        geometry.analyze_geometry(context, metadata)

    assert error.value.code == "MAIN_VIEW_HAS_HOLES"


def test_two_equally_credible_main_views_are_rejected(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
    drawing = tmp_path / "two-main-views.dxf"
    _save_geometry_source(drawing, duplicate_main=True)
    context, metadata = _load_geometry_context(drawing)

    with pytest.raises(PLSplitError) as error:
        geometry.analyze_geometry(context, metadata)

    assert error.value.code == "MAIN_VIEW_AMBIGUOUS"


def test_missing_closed_section_is_rejected(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
    drawing = tmp_path / "missing-section.dxf"
    _save_geometry_source(drawing, include_section=False)
    context, metadata = _load_geometry_context(drawing)

    with pytest.raises(PLSplitError) as error:
        geometry.analyze_geometry(context, metadata)

    assert error.value.code == "SECTION_MISSING"


def test_non_x_main_axis_is_rejected(tmp_path: Path) -> None:
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
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


def test_sub_tolerance_boundary_noise_is_not_emitted_as_a_zero_length_cut(
    tmp_path: Path,
) -> None:
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
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
    contracts = importlib.import_module("steel_dxf_split.pl.contracts")
    geometry = importlib.import_module("steel_dxf_split.pl.geometry")
    development = importlib.import_module("steel_dxf_split.pl.development")
    context, metadata = _load_geometry_context(path)
    outline, section = geometry.analyze_geometry(context, metadata)
    entities, metrics = development.transform_outline(
        outline.outer_entities,
        projection_length_mm=outline.projection_length_mm,
        surface_lengths_mm=section.equivalent_surface_lengths_mm,
        bom_length_mm=metadata.bom_length_mm,
        anchor_x_mm=outline.anchor_x_mm,
    )
    return contracts.DevelopedPlate(
        metadata=metadata,
        outline=outline,
        section=section,
        transformed_entities=entities,
        metrics=metrics,
    )


def test_writer_emits_clean_r2007_mm_layers_label_and_exact_ellipse(
    tmp_path: Path,
) -> None:
    writer = importlib.import_module("steel_dxf_split.pl.writer")
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
    assert len(saved.modelspace().query('ELLIPSE[layer=="PLATE_CUT"]')) == 1
    labels = list(saved.modelspace().query('TEXT[layer=="PART_LABEL"]'))
    assert [label.dxf.text for label in labels] == ["p=q6-b-62"]
    assert labels[0].dxf.style == "SplitChinese"
    assert saved.audit().has_errors is False
    assert result.min_x_mm == pytest.approx(0.0, abs=0.001)
    assert result.length_mm == pytest.approx(600.0, abs=0.001)
    assert result.width_mm == pytest.approx(300.0, abs=0.001)
    assert result.label == "p=q6-b-62"


def test_saved_output_validation_rejects_non_manufacturing_entities(
    tmp_path: Path,
) -> None:
    writer = importlib.import_module("steel_dxf_split.pl.writer")
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
