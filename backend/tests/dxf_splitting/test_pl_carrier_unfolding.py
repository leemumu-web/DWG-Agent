from dataclasses import replace
from decimal import Decimal
from math import asin, pi
from pathlib import Path

import ezdxf
import pytest
from ezdxf.entities import DXFEntity
from shapely.geometry import Polygon
from steel_dxf_split.pl.contracts import (
    DevelopedPlate,
    LongitudinalIntervalEvidence,
    LongitudinalProof,
    PlateOutline,
    PLMetadata,
    PLSplitError,
    SectionProof,
    StationBand,
)
from steel_dxf_split.pl.development import (
    _merge_collinear_lines,
    calculate_target,
    ceil_tenth_mm,
    transform_outline,
)
from steel_dxf_split.pl.geometry import flatten_entity, validate_closed_outline
from steel_dxf_split.pl.longitudinal import (
    analyze_longitudinal_outline,
    canonical_boundary_pieces,
    select_carrier_zone,
)


def _interval(
    index: int,
    start: float,
    end: float,
    *,
    upper_dy: float = 0.0,
    lower_dy: float = 0.0,
    end_feature: bool = False,
) -> LongitudinalIntervalEvidence:
    return LongitudinalIntervalEvidence(
        index=index,
        left_station=StationBand(index, start, start, (index,)),
        right_station=StationBand(index + 1, end, end, (index + 1,)),
        upper_entity_indices=(index * 2,),
        lower_entity_indices=(index * 2 + 1,),
        upper_span_mm=end - start,
        lower_span_mm=end - start,
        upper_delta_y_mm=upper_dy,
        lower_delta_y_mm=lower_dy,
        is_end_feature=end_feature,
        is_turn_candidate=abs(upper_dy) > 0.001 and abs(lower_dy) > 0.001,
        source_handles=(f"u{index}", f"l{index}"),
    )


def _line_outline(
    points: tuple[tuple[float, float], ...],
) -> tuple[tuple[DXFEntity, ...], Polygon]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    entities = tuple(
        modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in zip(points, (*points[1:], points[0]), strict=True)
    )
    return entities, Polygon(points)


def _fragmented_rectangle() -> tuple[tuple[DXFEntity, ...], Polygon]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    courses = (
        ((0.0, 350.0), (800.0, 350.0)),
        ((800.0, 350.0), (1000.0, 350.0)),
        ((1000.0, 350.0), (1000.0, 0.0)),
        ((1000.0, 0.0), (800.0, 0.0)),
        ((800.0, 0.0), (0.0, 0.0)),
        ((0.0, 0.0), (0.0, 350.0)),
    )
    entities = tuple(
        modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in courses
    )
    polygon = Polygon(
        ((0.0, 0.0), (1000.0, 0.0), (1000.0, 350.0), (0.0, 350.0))
    )
    return entities, polygon


def _paired_outline(
    upper: tuple[tuple[float, float], ...],
    lower: tuple[tuple[float, float], ...],
) -> tuple[tuple[DXFEntity, ...], Polygon]:
    return _line_outline((*upper, *reversed(lower)))


def _mixed_curve_outline() -> tuple[tuple[DXFEntity, ...], Polygon]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    curve = modelspace.add_ellipse(
        (400.0, 50.0),
        (400.0, 0.0),
        0.125,
        3.0 * pi / 2.0,
        2.85 * pi,
        dxfattribs={"layer": "Part"},
    )
    curve_points = flatten_entity(curve, 0.001)
    lower_left = (curve_points[-1][0], 0.0)
    entities = (
        curve,
        modelspace.add_line(
            curve_points[-1],
            lower_left,
            dxfattribs={"layer": "Part"},
        ),
        modelspace.add_line(
            lower_left,
            curve_points[0],
            dxfattribs={"layer": "Part"},
        ),
    )
    return entities, validate_closed_outline(entities, tolerance_mm=0.1)


def _split_mixed_curve_outline() -> tuple[tuple[DXFEntity, ...], Polygon]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    lower_curve = modelspace.add_ellipse(
        (400.0, 50.0),
        (400.0, 0.0),
        0.125,
        3.0 * pi / 2.0,
        2.0 * pi,
        dxfattribs={"layer": "Part"},
    )
    upper_curve = modelspace.add_ellipse(
        (400.0, 50.0),
        (400.0, 0.0),
        0.125,
        2.0 * pi,
        2.85 * pi,
        dxfattribs={"layer": "Part"},
    )
    lower_points = flatten_entity(lower_curve, 0.001)
    upper_points = flatten_entity(upper_curve, 0.001)
    lower_left = (upper_points[-1][0], 0.0)
    entities = (
        lower_curve,
        upper_curve,
        modelspace.add_line(
            upper_points[-1],
            lower_left,
            dxfattribs={"layer": "Part"},
        ),
        modelspace.add_line(
            lower_left,
            lower_points[0],
            dxfattribs={"layer": "Part"},
        ),
    )
    return entities, validate_closed_outline(entities, tolerance_mm=0.1)


def _mixed_arc_overlapping_line_outline() -> tuple[tuple[DXFEntity, ...], Polygon]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    curve = modelspace.add_ellipse(
        (400.0, 50.0),
        (400.0, 0.0),
        0.125,
        3.0 * pi / 2.0,
        3.0 * pi,
        dxfattribs={"layer": "Part"},
    )
    curve_points = flatten_entity(curve, 0.001)
    lower_left = (curve_points[-1][0], 0.0)
    bottom_span = curve_points[0][0] - lower_left[0]
    entities = (
        curve,
        modelspace.add_line(
            curve_points[-1],
            lower_left,
            dxfattribs={"layer": "Part"},
        ),
        modelspace.add_line(
            lower_left,
            (lower_left[0] + bottom_span * 0.75, 0.0),
            dxfattribs={"layer": "Part"},
        ),
        modelspace.add_line(
            (lower_left[0] + bottom_span * 0.5, 0.0),
            curve_points[0],
            dxfattribs={"layer": "Part"},
        ),
    )
    return entities, validate_closed_outline(entities, tolerance_mm=0.1)


def _smooth_curve_course_outline(
    curve_kind: str,
    *,
    split: bool,
) -> tuple[tuple[DXFEntity, ...], Polygon]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    if curve_kind == "ARC":
        start = 180.0 * asin(0.6) / pi
        end = 180.0 - start
        split_at = 90.0

        def add_curve(first: float, second: float) -> DXFEntity:
            return modelspace.add_arc(
                (400.0, -200.0),
                500.0,
                first,
                second,
                dxfattribs={"layer": "Part"},
            )

    else:
        start = asin(0.6)
        end = pi - start
        split_at = pi / 2.0

        def add_curve(first: float, second: float) -> DXFEntity:
            return modelspace.add_ellipse(
                (400.0, -50.0),
                (500.0, 0.0),
                0.5,
                first,
                second,
                dxfattribs={"layer": "Part"},
            )

    spans = ((start, split_at), (split_at, end)) if split else ((start, end),)
    curves = tuple(add_curve(first, second) for first, second in spans)
    right_top = flatten_entity(curves[0], 0.001)[0]
    left_top = flatten_entity(curves[-1], 0.001)[-1]
    left_bottom = (left_top[0], 0.0)
    right_bottom = (right_top[0], 0.0)
    entities = (
        *curves,
        modelspace.add_line(left_top, left_bottom, dxfattribs={"layer": "Part"}),
        modelspace.add_line(left_bottom, right_bottom, dxfattribs={"layer": "Part"}),
        modelspace.add_line(right_bottom, right_top, dxfattribs={"layer": "Part"}),
    )
    return entities, validate_closed_outline(entities, tolerance_mm=0.1)


def _q7_like_outline() -> tuple[tuple[DXFEntity, ...], Polygon]:
    return _paired_outline(
        (
            (0.0, 100.0),
            (554.065640, 100.0),
            (851.993500, 80.0),
            (1154.065614, 80.0),
        ),
        (
            (0.0, 0.0),
            (554.065640, 0.0),
            (851.993500, 20.0),
            (1154.065614, 20.0),
        ),
    )


def _curve_station_band_outline() -> tuple[tuple[DXFEntity, ...], Polygon]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    entities = (
        modelspace.add_line((0.0, 300.0), (300.0, 300.0)),
        modelspace.add_arc((500.0, 300.0), 200.0, 180.0, 270.0),
        modelspace.add_line((500.0, 100.0), (900.0, 100.0)),
        modelspace.add_line((900.0, 100.0), (900.0, 20.0)),
        modelspace.add_line((900.0, 20.0), (505.0, 20.0)),
        modelspace.add_line((505.0, 20.0), (300.0, 0.0)),
        modelspace.add_line((300.0, 0.0), (0.0, 0.0)),
        modelspace.add_line((0.0, 0.0), (0.0, 300.0)),
    )
    return entities, validate_closed_outline(entities, tolerance_mm=0.1)


def _crossing_curve_outline(
    curve_kind: str,
) -> tuple[tuple[DXFEntity, ...], LongitudinalProof]:
    document = ezdxf.new()
    modelspace = document.modelspace()
    if curve_kind == "ARC":
        curve = modelspace.add_arc((450.0, 100.0), 450.0, 0.0, 180.0)
    else:
        curve = modelspace.add_ellipse(
            (450.0, 100.0),
            (450.0, 0.0),
            0.5,
            0.0,
            pi,
        )
    entities = (
        curve,
        modelspace.add_line((0.0, 100.0), (0.0, 0.0)),
        modelspace.add_line((0.0, 0.0), (300.0, 0.0)),
        modelspace.add_line((300.0, 0.0), (600.0, 0.0)),
        modelspace.add_line((600.0, 0.0), (900.0, 0.0)),
        modelspace.add_line((900.0, 0.0), (900.0, 100.0)),
    )
    stations = (
        StationBand(0, 0.0, 0.0, (0, 1, 2)),
        StationBand(1, 300.0, 300.0, (0, 2, 3)),
        StationBand(2, 600.0, 600.0, (0, 3, 4)),
        StationBand(3, 900.0, 900.0, (0, 4, 5)),
    )
    intervals = tuple(
        LongitudinalIntervalEvidence(
            index=index,
            left_station=stations[index],
            right_station=stations[index + 1],
            upper_entity_indices=(0,),
            lower_entity_indices=(index + 2,),
            upper_span_mm=300.0,
            lower_span_mm=300.0,
            upper_delta_y_mm=0.0,
            lower_delta_y_mm=0.0,
            is_end_feature=False,
            is_turn_candidate=index == 1,
            source_handles=(f"upper-{index}", f"lower-{index}"),
        )
        for index in range(3)
    )
    return entities, LongitudinalProof(
        intervals=intervals,
        carrier_interval_indices=(1,),
        selection_reason="paired_visible_turn",
    )


def _crossing_curve_developed(
    curve_kind: str,
) -> tuple[tuple[DXFEntity, ...], DevelopedPlate]:
    source_entities, proof = _crossing_curve_outline(curve_kind)
    source_polygon = validate_closed_outline(source_entities, tolerance_mm=0.1)
    transformed, metrics = transform_outline(
        source_entities,
        longitudinal=proof,
        projection_length_mm=900.0,
        k_length_mm=920.0,
        bom_length_mm=900.0,
        anchor_x_mm=0.0,
    )
    return source_entities, DevelopedPlate(
        metadata=PLMetadata("q7-b-404", 30.0, 550.0, 900.0),
        outline=PlateOutline(
            outer_entities=source_entities,
            polygon=source_polygon,
            projection_length_mm=900.0,
            width_mm=550.0,
            anchor_x_mm=0.0,
            source_handles=(),
            candidate_count=1,
        ),
        section=SectionProof(
            polygon=Polygon(((0.0, -40.0), (920.0, -40.0), (920.0, -10.0), (0.0, -10.0))),
            k_length_mm=920.0,
            equivalent_surface_lengths_mm=(920.0, 920.0),
            proof_method="section_area_over_thickness_k_half",
            source_handles=(),
            candidate_count=1,
        ),
        longitudinal=proof,
        transformed_entities=transformed,
        metrics=metrics,
    )


def _q7_like_developed() -> DevelopedPlate:
    source_entities, source_polygon = _q7_like_outline()
    proof = analyze_longitudinal_outline(
        source_entities,
        source_polygon,
        thickness_mm=30.0,
    )
    transformed, metrics = transform_outline(
        source_entities,
        longitudinal=proof,
        projection_length_mm=1154.065614,
        k_length_mm=1162.124078,
        bom_length_mm=1162.0,
        anchor_x_mm=0.0,
    )
    return DevelopedPlate(
        metadata=PLMetadata("q7-b-404", 30.0, 100.0, 1162.0),
        outline=PlateOutline(
            outer_entities=source_entities,
            polygon=source_polygon,
            projection_length_mm=1154.065614,
            width_mm=100.0,
            anchor_x_mm=0.0,
            source_handles=(),
            candidate_count=1,
        ),
        section=SectionProof(
            polygon=Polygon(
                ((0.0, -40.0), (1162.124078, -40.0), (1162.124078, -10.0), (0.0, -10.0))
            ),
            k_length_mm=1162.124078,
            equivalent_surface_lengths_mm=(1162.124078, 1162.124078),
            proof_method="section_area_over_thickness_k_half",
            source_handles=(),
            candidate_count=1,
        ),
        longitudinal=proof,
        transformed_entities=transformed,
        metrics=metrics,
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (Decimal("470.0"), Decimal("470.0")),
        (Decimal("470.0000001"), Decimal("470.1")),
        (Decimal("470.0999999"), Decimal("470.1")),
        (Decimal("470.1000001"), Decimal("470.2")),
    ],
)
def test_strict_tenth_ceiling_never_absorbs_a_positive_residual(
    source: Decimal,
    expected: Decimal,
) -> None:
    assert ceil_tenth_mm(source) == expected


def test_q7_b_404_uses_one_total_ceiling_without_per_interval_growth() -> None:
    target = calculate_target(
        projection_length_mm=1154.065614079,
        k_length_mm=1162.124078060,
        bom_length_mm=1162.0,
    )

    assert target.raw_length_mm == pytest.approx(1162.124078060)
    assert target.target_length_mm == pytest.approx(1162.2)
    assert target.total_extension_mm == pytest.approx(8.134385921)
    assert target.total_extension_mm < 8.2


def test_slanted_end_station_does_not_remain_as_an_output_line_split() -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 350.0),
            (671.577935, 350.0),
            (671.577935, 0.0),
            (14.089207, 0.0),
        )
    )
    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=40.0)

    transformed, metrics = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=671.577935,
        k_length_mm=815.949,
        bom_length_mm=816.0,
        anchor_x_mm=0.0,
    )

    assert metrics.target_length_mm == pytest.approx(816.0)
    assert tuple(entity.dxftype() for entity in transformed) == ("LINE",) * 4
    actual = validate_closed_outline(transformed, tolerance_mm=0.1)
    expected = Polygon(
        (
            (0.0, 350.0),
            (816.0, 350.0),
            (816.0, 0.0),
            (14.089207, 0.0),
        )
    )
    assert actual.symmetric_difference(expected).area <= 0.1


def test_cross_source_collinear_courses_are_one_output_line() -> None:
    entities, polygon = _fragmented_rectangle()
    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=35.0)

    transformed, metrics = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=1000.0,
        k_length_mm=1100.0,
        bom_length_mm=1100.0,
        anchor_x_mm=0.0,
    )

    assert metrics.target_length_mm == pytest.approx(1100.0)
    assert tuple(entity.dxftype() for entity in transformed) == ("LINE",) * 4
    actual = validate_closed_outline(transformed)
    expected = Polygon(
        ((0.0, 0.0), (1100.0, 0.0), (1100.0, 350.0), (0.0, 350.0))
    )
    assert actual.symmetric_difference(expected).area <= 0.001


def test_uniform_projection_fallback_coalesces_cross_source_courses() -> None:
    entities, polygon = _fragmented_rectangle()
    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=35.0)

    transformed, metrics = transform_outline(
        entities,
        longitudinal=replace(proof, selection_reason="uniform_projection_fallback"),
        projection_length_mm=1000.0,
        k_length_mm=1000.0,
        bom_length_mm=1000.0,
        anchor_x_mm=0.0,
    )

    assert metrics.target_length_mm == pytest.approx(1000.0)
    assert tuple(entity.dxftype() for entity in transformed) == ("LINE",) * 4
    actual = validate_closed_outline(transformed)
    assert actual.symmetric_difference(polygon).area <= 0.001


def test_uniform_projection_fallback_removes_contained_duplicate_line() -> None:
    entities, polygon = _line_outline(
        ((0.0, 0.0), (1000.0, 0.0), (1000.0, 350.0), (0.0, 350.0))
    )
    duplicate = entities[0].copy()
    duplicate.dxf.start = (800.0, 0.0)
    proof = analyze_longitudinal_outline(
        (*entities, duplicate), polygon, thickness_mm=35.0
    )

    transformed, _ = transform_outline(
        (*entities, duplicate),
        longitudinal=replace(proof, selection_reason="uniform_projection_fallback"),
        projection_length_mm=1000.0,
        k_length_mm=1000.0,
        bom_length_mm=1000.0,
        anchor_x_mm=0.0,
    )

    assert tuple(entity.dxftype() for entity in transformed) == ("LINE",) * 4
    actual = validate_closed_outline(transformed)
    assert actual.symmetric_difference(polygon).area <= 0.001


def test_contained_collinear_overlap_is_not_merged_as_adjacent_courses() -> None:
    entities, _ = _line_outline(
        ((0.0, 0.0), (1000.0, 0.0), (1000.0, 350.0), (0.0, 350.0))
    )
    contained = entities[0].copy()
    contained.dxf.start = (800.0, 0.0)

    assert _merge_collinear_lines(entities[0], contained) is None


def test_q7_piecewise_transform_grows_only_the_carrier_interval() -> None:
    entities, polygon = _q7_like_outline()
    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    transformed, metrics = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=1154.065614,
        k_length_mm=1162.124078,
        bom_length_mm=1162.0,
        anchor_x_mm=0.0,
    )

    assert metrics.target_length_mm == pytest.approx(1162.2)
    assert metrics.total_extension_mm == pytest.approx(8.134386, abs=1e-6)
    assert metrics.carrier_interval_indices == (1,)
    assert metrics.intervals[0].output_upper_span_mm == pytest.approx(
        metrics.intervals[0].source_upper_span_mm
    )
    assert metrics.intervals[1].output_upper_span_mm == pytest.approx(
        metrics.intervals[1].source_upper_span_mm + metrics.total_extension_mm
    )
    assert metrics.intervals[2].output_upper_span_mm == pytest.approx(
        metrics.intervals[2].source_upper_span_mm
    )
    assert metrics.intervals[2].downstream_shift_mm == pytest.approx(
        8.134386,
        abs=1e-6,
    )
    station_xs = tuple(
        sorted(
            {
                round(float(point.x), 6)
                for entity in transformed
                for point in (entity.dxf.start, entity.dxf.end)
            }
        )
    )
    assert station_xs == pytest.approx(
        (0.0, 554.065640, 860.127886, 1162.2),
        abs=0.001,
    )


def test_curve_station_band_and_downstream_are_transformed_by_region() -> None:
    entities, polygon = _curve_station_band_outline()
    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)
    source_upper = entities[2]
    source_lower = entities[4]

    transformed, metrics = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=900.0,
        k_length_mm=920.0,
        bom_length_mm=900.0,
        anchor_x_mm=0.0,
    )

    assert proof.carrier_interval_indices == (1,)
    assert any(entity.dxftype() == "ELLIPSE" for entity in transformed)
    output_upper = next(
        entity
        for entity in transformed
        if entity.dxftype() == "LINE"
        and entity.dxf.start.y == pytest.approx(100.0)
        and entity.dxf.end.y == pytest.approx(100.0)
    )
    output_lower = next(
        entity
        for entity in transformed
        if entity.dxftype() == "LINE"
        and entity.dxf.start.y == pytest.approx(20.0)
        and entity.dxf.end.y == pytest.approx(20.0)
    )
    right_upper_x_in = min(float(source_upper.dxf.start.x), float(source_upper.dxf.end.x))
    right_lower_x_in = min(float(source_lower.dxf.start.x), float(source_lower.dxf.end.x))
    right_upper_x_out = min(float(output_upper.dxf.start.x), float(output_upper.dxf.end.x))
    right_lower_x_out = min(float(output_lower.dxf.start.x), float(output_lower.dxf.end.x))
    assert right_upper_x_out - right_upper_x_in == pytest.approx(metrics.total_extension_mm)
    assert right_lower_x_out - right_lower_x_in == pytest.approx(metrics.total_extension_mm)
    downstream_span_in = abs(float(source_upper.dxf.end.x - source_upper.dxf.start.x))
    downstream_span_out = abs(float(output_upper.dxf.end.x - output_upper.dxf.start.x))
    assert downstream_span_out == pytest.approx(downstream_span_in)
    assert (float(output_upper.dxf.start.y), float(output_upper.dxf.end.y)) == pytest.approx(
        (float(source_upper.dxf.start.y), float(source_upper.dxf.end.y))
    )


@pytest.mark.parametrize("curve_kind", ("ARC", "ELLIPSE"))
def test_transform_exactly_splits_native_curves_at_station_boundaries(
    curve_kind: str,
) -> None:
    entities, proof = _crossing_curve_outline(curve_kind)

    transformed, metrics = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=900.0,
        k_length_mm=920.0,
        bom_length_mm=900.0,
        anchor_x_mm=0.0,
    )

    curve_pieces = tuple(entity for entity in transformed if entity.dxftype() in {"ARC", "ELLIPSE"})
    assert len(curve_pieces) == 3
    if curve_kind == "ARC":
        assert tuple(entity.dxftype() for entity in curve_pieces).count("ELLIPSE") == 1
    endpoint_xs = tuple(
        sorted(
            {
                round(float(point[0]), 6)
                for entity in curve_pieces
                for point in (flatten_entity(entity)[0], flatten_entity(entity)[-1])
            }
        )
    )
    assert endpoint_xs == pytest.approx((0.0, 300.0, 620.0, 920.0), abs=0.001)
    assert metrics.intervals[1].output_upper_span_mm == pytest.approx(320.0)


def test_saved_validation_accepts_additional_native_segmentation(
    tmp_path: Path,
) -> None:
    from steel_dxf_split.pl.writer import validate_saved_pl_dxf, write_pl_dxf

    source_entities, developed = _crossing_curve_developed("ARC")
    transformed = developed.transformed_entities
    output = tmp_path / "q7-b-404.dxf"

    write_pl_dxf(developed, output)
    saved = ezdxf.readfile(output)
    saved_plate_entities = tuple(
        entity for entity in saved.modelspace() if entity.dxf.layer == "PLATE_CUT"
    )
    source_curve_count = sum(
        entity.dxftype() in {"ARC", "ELLIPSE"} for entity in source_entities
    )
    saved_curve_count = sum(
        entity.dxftype() in {"ARC", "ELLIPSE"} for entity in saved_plate_entities
    )
    assert saved_curve_count == source_curve_count + 2
    line = max(
        saved.modelspace().query('LINE[layer=="PLATE_CUT"]'),
        key=lambda entity: abs(float(entity.dxf.end.x - entity.dxf.start.x)),
    )
    start = line.dxf.start
    end = line.dxf.end
    midpoint = start.lerp(end, factor=0.5)
    saved.modelspace().delete_entity(line)
    saved.modelspace().add_line(start, midpoint, dxfattribs={"layer": "PLATE_CUT"})
    saved.modelspace().add_line(midpoint, end, dxfattribs={"layer": "PLATE_CUT"})
    saved.saveas(output)

    result = validate_saved_pl_dxf(output, developed)
    reopened = ezdxf.readfile(output)
    labels = list(reopened.modelspace().query('TEXT[layer=="PART_LABEL"]'))

    assert len(reopened.modelspace().query('*[layer=="PLATE_CUT"]')) > len(transformed)
    assert len(labels) == 1
    assert labels[0].dxf.text == "p=q7-b-404"
    assert labels[0].dxf.height == pytest.approx(30.0)
    assert result.length_mm == pytest.approx(920.0, abs=0.001)
    assert result.width_mm == pytest.approx(550.0, abs=0.001)
    assert reopened.audit().has_errors is False


def test_saved_validation_rejects_a_shifted_carrier_station(tmp_path: Path) -> None:
    from steel_dxf_split.pl.writer import write_pl_dxf

    _, developed = _crossing_curve_developed("ARC")
    carrier_right_x = sum(
        interval.output_lower_span_mm for interval in developed.metrics.intervals[:2]
    )
    tampered: list[DXFEntity] = []
    for entity in developed.transformed_entities:
        if entity.dxftype() != "LINE":
            tampered.append(entity.copy())
            continue
        start = entity.dxf.start
        end = entity.dxf.end
        delta_x = float(end.x - start.x)
        if abs(delta_x) <= 1e-9:
            tampered.append(entity.copy())
            continue
        parameter = (carrier_right_x - float(start.x)) / delta_x
        if not 1e-6 < parameter < 1.0 - 1e-6:
            tampered.append(entity.copy())
            continue
        station = start.lerp(end, factor=parameter)
        shifted = (
            float(station.x) + 5.0,
            float(station.y) + 5.0,
            float(station.z),
        )
        first = entity.copy()
        first.dxf.end = shifted
        second = entity.copy()
        second.dxf.start = shifted
        tampered.extend((first, second))
    altered = replace(developed, transformed_entities=tuple(tampered))

    with pytest.raises(PLSplitError) as error:
        write_pl_dxf(altered, tmp_path / "shifted-station.dxf")

    assert error.value.code == "OUTPUT_INTERVAL_CONTRACT"


def test_saved_validation_does_not_use_upper_fragments_for_a_missing_lower_station(
    tmp_path: Path,
) -> None:
    from steel_dxf_split.pl.writer import validate_saved_pl_dxf, write_pl_dxf

    developed = _q7_like_developed()
    output = tmp_path / "chain-confusion.dxf"
    write_pl_dxf(developed, output)
    saved = ezdxf.readfile(output)
    carrier_right_x = sum(
        interval.output_lower_span_mm for interval in developed.metrics.intervals[:2]
    )
    upper_downstream = next(
        entity
        for entity in saved.modelspace().query('LINE[layer=="PLATE_CUT"]')
        if abs(float(entity.dxf.start.x) - carrier_right_x) <= 0.001
        and abs(float(entity.dxf.start.y) - 80.0) <= 0.001
        and float(entity.dxf.end.x) > carrier_right_x
    )
    for entity in saved.modelspace().query('LINE[layer=="PLATE_CUT"]'):
        for attribute in ("start", "end"):
            point = entity.dxf.get(attribute)
            if abs(float(point.x) - carrier_right_x) <= 0.001 and float(point.y) < 50.0:
                entity.dxf.set(attribute, (float(point.x) + 5.0, point.y, point.z))
    start = upper_downstream.dxf.start
    end = upper_downstream.dxf.end
    saved.modelspace().delete_entity(upper_downstream)
    saved.modelspace().add_line(
        start,
        (float(start.x) + 40.0, start.y),
        dxfattribs={"layer": "PLATE_CUT"},
    )
    saved.modelspace().add_line(
        (float(start.x) + 40.0, start.y),
        (start.x, float(start.y) - 10.0),
        dxfattribs={"layer": "PLATE_CUT"},
    )
    saved.modelspace().add_line(
        (start.x, float(start.y) - 10.0),
        end,
        dxfattribs={"layer": "PLATE_CUT"},
    )
    saved.saveas(output)
    reopened = ezdxf.readfile(output)
    plate = tuple(reopened.modelspace().query('*[layer=="PLATE_CUT"]'))
    label = list(reopened.modelspace().query('TEXT[layer=="PART_LABEL"]'))
    polygon = validate_closed_outline(plate)

    assert polygon.bounds == pytest.approx((0.0, 0.0, 1162.2, 100.0), abs=0.001)
    assert len(label) == 1
    assert label[0].dxf.text == "p=q7-b-404"
    assert label[0].dxf.height == pytest.approx(30.0)
    assert reopened.audit().has_errors is False
    with pytest.raises(PLSplitError) as error:
        validate_saved_pl_dxf(output, developed)

    assert error.value.code == "OUTPUT_INTERVAL_CONTRACT"


def test_visible_middle_turn_wins_without_using_part_identity() -> None:
    intervals = (
        _interval(0, 0.0, 554.06564),
        _interval(1, 554.06564, 851.99350, upper_dy=-50.0, lower_dy=50.0),
        _interval(2, 851.99350, 1154.065614),
    )

    assert select_carrier_zone(intervals) == ((1,), "paired_visible_turn")


def test_flat_outline_uses_unique_longest_body_interval() -> None:
    intervals = (
        _interval(0, 0.0, 1176.513),
        _interval(1, 1176.513, 1200.974, end_feature=True),
    )

    assert select_carrier_zone(intervals) == ((0,), "unique_longest_body")


def test_disjoint_turn_candidates_fail_closed() -> None:
    intervals = (
        _interval(0, 0.0, 200.0, upper_dy=20.0, lower_dy=-20.0),
        _interval(1, 200.0, 400.0),
        _interval(2, 400.0, 600.0, upper_dy=-20.0, lower_dy=20.0),
    )

    with pytest.raises(PLSplitError) as error:
        select_carrier_zone(intervals)

    assert error.value.code == "CARRIER_AMBIGUOUS"


def test_adjacent_turn_fragments_form_one_carrier_zone() -> None:
    intervals = (
        _interval(0, 0.0, 100.0),
        _interval(1, 100.0, 140.0, upper_dy=12.0, lower_dy=-12.0),
        _interval(2, 140.0, 180.0, upper_dy=8.0, lower_dy=-8.0),
        _interval(3, 180.0, 300.0),
    )

    assert select_carrier_zone(intervals) == ((1, 2), "paired_visible_turn")


def test_disjoint_turn_groups_are_ambiguous_regardless_of_relative_length() -> None:
    intervals = (
        _interval(0, 0.0, 4.0, upper_dy=0.2, lower_dy=-0.2),
        _interval(1, 4.0, 34.0, upper_dy=30.0, lower_dy=-30.0),
        _interval(2, 34.0, 35.0, upper_dy=4.0, lower_dy=-4.0),
        _interval(3, 35.0, 485.0),
        _interval(4, 485.0, 785.0, upper_dy=-60.0, lower_dy=60.0),
        _interval(5, 785.0, 1085.0),
    )

    with pytest.raises(PLSplitError) as error:
        select_carrier_zone(intervals)

    assert error.value.code == "CARRIER_AMBIGUOUS"


def test_rounded_terminal_profile_is_end_topology_before_carrier_selection() -> None:
    entities, polygon = _paired_outline(
        (
            (0.0, 250.0),
            (3.67, 250.19),
            (34.82, 281.34),
            (35.01, 285.0),
            (484.0, 285.0),
            (784.0, 225.0),
            (1084.0, 225.0),
        ),
        (
            (0.0, 0.0),
            (3.67, -0.19),
            (34.82, -31.34),
            (35.01, -35.0),
            (484.0, -35.0),
            (784.0, 25.0),
            (1084.0, 25.0),
        ),
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=14.0)

    assert proof.carrier_interval_indices == (4,)
    assert tuple(interval.index for interval in proof.intervals) == tuple(range(6))
    assert all(interval.is_end_feature for interval in proof.intervals[:3])
    assert all(interval.is_turn_candidate for interval in proof.intervals[:3])


def test_single_parallel_sloped_course_remains_a_body_interval() -> None:
    entities, polygon = _paired_outline(
        ((0.0, 100.0), (800.0, 200.0)),
        ((0.0, 0.0), (800.0, 100.0)),
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (0,)
    assert tuple(interval.index for interval in proof.intervals) == (0,)
    assert not proof.intervals[0].is_end_feature
    assert proof.intervals[0].is_turn_candidate


def test_longest_body_tie_within_one_tenth_fails_closed() -> None:
    intervals = (
        _interval(0, 0.0, 500.0),
        _interval(1, 500.0, 999.95),
    )

    with pytest.raises(PLSplitError) as error:
        select_carrier_zone(intervals)

    assert error.value.code == "CARRIER_AMBIGUOUS"


@pytest.mark.parametrize(
    ("upper", "lower", "expected_index", "expected_count"),
    [
        (
            ((0.0, 100.0), (300.0, 80.0), (600.0, 80.0), (900.0, 80.0)),
            ((0.0, 0.0), (300.0, 20.0), (600.0, 20.0), (900.0, 20.0)),
            0,
            2,
        ),
        (
            ((0.0, 100.0), (300.0, 100.0), (600.0, 80.0), (900.0, 80.0)),
            ((0.0, 0.0), (300.0, 0.0), (600.0, 20.0), (900.0, 20.0)),
            1,
            3,
        ),
        (
            ((0.0, 100.0), (300.0, 100.0), (600.0, 100.0), (900.0, 80.0)),
            ((0.0, 0.0), (300.0, 0.0), (600.0, 0.0), (900.0, 20.0)),
            1,
            2,
        ),
    ],
    ids=("first", "middle", "last"),
)
def test_longitudinal_visible_turn_selects_its_relative_position(
    upper: tuple[tuple[float, float], ...],
    lower: tuple[tuple[float, float], ...],
    expected_index: int,
    expected_count: int,
) -> None:
    entities, polygon = _paired_outline(upper, lower)

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (expected_index,)
    assert proof.selection_reason == "paired_visible_turn"
    assert tuple(interval.index for interval in proof.intervals) == tuple(range(expected_count))


def test_longitudinal_flat_body_excludes_a_short_unmatched_end_tab() -> None:
    entities, polygon = _paired_outline(
        ((0.0, 100.0), (800.0, 100.0)),
        ((0.0, 0.0), (800.0, 0.0), (830.0, 20.0)),
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"
    assert tuple(interval.index for interval in proof.intervals) == (0, 1)
    assert proof.intervals[1].is_end_feature


def test_longitudinal_proof_is_independent_of_test_layer_part_identity() -> None:
    entities, polygon = _paired_outline(
        ((0.0, 100.0), (300.0, 100.0), (600.0, 80.0), (900.0, 80.0)),
        ((0.0, 0.0), (300.0, 0.0), (600.0, 20.0), (900.0, 20.0)),
    )
    proofs = tuple(
        analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)
        for _part_number in ("unrelated-alpha", "unrelated-omega")
    )

    assert proofs[0] == proofs[1]


def test_station_band_wider_than_thickness_plus_one_tenth_is_rejected() -> None:
    entities, polygon = _paired_outline(
        ((0.0, 100.0), (400.0, 100.0), (800.0, 80.0)),
        ((0.0, 0.0), (430.2, 0.0), (800.0, 20.0)),
    )

    with pytest.raises(PLSplitError) as error:
        analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert error.value.code == "STATION_BAND_TOO_WIDE"


def test_station_band_equal_to_thickness_plus_one_tenth_is_allowed() -> None:
    entities, polygon = _paired_outline(
        ((0.0, 100.0), (400.0, 100.0), (800.0, 80.0)),
        ((0.0, 0.0), (430.1, 0.0), (800.0, 20.0)),
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.intervals[0].right_station.upper_x_mm == pytest.approx(400.0)
    assert proof.intervals[0].right_station.lower_x_mm == pytest.approx(430.1)


def test_longitudinal_asymmetric_collinear_fragmentation_does_not_add_stations() -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 100.0),
            (400.0, 100.0),
            (800.0, 100.0),
            (800.0, 0.0),
            (0.0, 0.0),
        )
    )
    unsplit_entities, unsplit_polygon = _line_outline(
        ((0.0, 100.0), (800.0, 100.0), (800.0, 0.0), (0.0, 0.0))
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)
    unsplit = analyze_longitudinal_outline(
        unsplit_entities,
        unsplit_polygon,
        thickness_mm=30.0,
    )

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"
    assert unsplit.carrier_interval_indices == proof.carrier_interval_indices
    assert unsplit.selection_reason == proof.selection_reason
    assert tuple(interval.index for interval in proof.intervals) == (0,)
    assert tuple(interval.index for interval in unsplit.intervals) == (0,)
    assert proof.intervals[0].upper_span_mm == pytest.approx(800.0)
    assert proof.intervals[0].lower_span_mm == pytest.approx(800.0)
    assert proof.intervals[0].left_station.upper_x_mm == pytest.approx(
        unsplit.intervals[0].left_station.upper_x_mm
    )
    assert proof.intervals[0].left_station.lower_x_mm == pytest.approx(
        unsplit.intervals[0].left_station.lower_x_mm
    )
    assert proof.intervals[0].right_station.upper_x_mm == pytest.approx(
        unsplit.intervals[0].right_station.upper_x_mm
    )
    assert proof.intervals[0].right_station.lower_x_mm == pytest.approx(
        unsplit.intervals[0].right_station.lower_x_mm
    )
    assert set(proof.intervals[0].upper_entity_indices) >= {0, 1}


def test_nondirect_terminal_fragment_does_not_create_a_station() -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 100.0),
            (800.0, 100.0),
            (800.0, 0.0),
            (780.0, 0.0),
            (0.0, 0.0),
        )
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"
    assert tuple(interval.index for interval in proof.intervals) == (0,)


@pytest.mark.parametrize(
    ("order", "reverse_directions"),
    [
        ((4, 3, 2, 1, 0), False),
        ((2, 0, 4, 1, 3), True),
    ],
    ids=("reversed-entities", "shuffled-reversed-directions"),
)
def test_longitudinal_fragmented_outline_is_entity_order_independent(
    order: tuple[int, ...],
    reverse_directions: bool,
) -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 100.0),
            (400.0, 100.0),
            (800.0, 100.0),
            (800.0, 0.0),
            (0.0, 0.0),
        )
    )
    if reverse_directions:
        for entity in entities:
            start = entity.dxf.start
            entity.dxf.start = entity.dxf.end
            entity.dxf.end = start
    reordered = tuple(entities[index] for index in order)

    proof = analyze_longitudinal_outline(reordered, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (0,)
    assert tuple(interval.index for interval in proof.intervals) == (0,)
    assert proof.intervals[0].upper_span_mm == pytest.approx(800.0)
    assert proof.intervals[0].lower_span_mm == pytest.approx(800.0)


def test_wide_paired_turn_station_cannot_be_recast_as_independent_ledges() -> None:
    entities, polygon = _paired_outline(
        ((0.0, 100.0), (400.0, 100.0), (800.0, 80.0)),
        ((0.0, 0.0), (500.0, 0.0), (800.0, 20.0)),
    )

    with pytest.raises(PLSplitError) as error:
        analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert error.value.code == "STATION_BAND_TOO_WIDE"


def test_end_near_layout_without_a_direct_chain_fails_closed() -> None:
    entities, polygon = _paired_outline(
        (
            (0.0, 100.0),
            (10.0, 100.0),
            (20.0, 105.0),
            (400.0, 105.0),
            (800.0, 95.0),
        ),
        ((0.0, 0.0), (10.0, 5.0), (25.0, 5.0), (800.0, 25.0)),
    )

    with pytest.raises(PLSplitError) as error:
        analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert error.value.code == "LONGITUDINAL_TOPOLOGY"


def test_direct_end_chain_topology_projects_opposite_independent_features() -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 100.0),
            (400.0, 110.0),
            (790.0, 100.0),
            (800.0, 0.0),
            (400.0, 10.0),
            (10.0, 0.0),
        ),
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    stations = (
        *(interval.left_station for interval in proof.intervals),
        proof.intervals[-1].right_station,
    )
    assert tuple((station.upper_x_mm, station.lower_x_mm) for station in stations) == (
        (0.0, 0.0),
        (10.0, 10.0),
        (400.0, 400.0),
        (790.0, 800.0),
    )
    assert proof.carrier_interval_indices == (2,)
    assert proof.selection_reason == "paired_visible_turn"


def test_end_proximity_cannot_prove_opposite_ledges_are_independent() -> None:
    entities, polygon = _paired_outline(
        (
            (0.0, 100.0),
            (10.0, 100.0),
            (20.0, 105.0),
            (70.0, 105.0),
            (80.0, 100.0),
        ),
        (
            (0.0, 0.0),
            (10.0, 5.0),
            (60.0, 5.0),
            (70.0, 0.0),
            (80.0, 0.0),
        ),
    )

    with pytest.raises(PLSplitError) as error:
        analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert error.value.code == "STATION_BAND_TOO_WIDE"


def test_terminal_opposite_chain_wobble_is_a_strict_turn_but_not_body() -> None:
    document = ezdxf.new()
    modelspace = document.modelspace()
    entities = tuple(
        modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in (
            ((902.999, 275.0), (602.5, 275.0)),
            ((302.5, 0.0), (303.0, 0.0)),
            ((303.0, 0.0), (603.0, 75.0)),
            ((602.5, 275.0), (303.0, 350.0)),
            ((603.0, 75.0), (903.0, 75.002)),
            ((903.0, 75.002), (902.999, 275.0)),
            ((0.0, 0.0), (0.0, 350.0)),
            ((0.0, 350.0), (303.0, 350.0)),
            ((302.5, 0.0), (0.0, 0.0)),
            ((603.0, 75.0), (602.5, 74.9)),
        )
    )
    polygon = validate_closed_outline(entities, tolerance_mm=0.1)

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (1,)
    assert proof.selection_reason == "paired_visible_turn"
    assert tuple(interval.index for interval in proof.intervals) == (0, 1, 2)
    assert proof.intervals[2].is_turn_candidate
    assert proof.intervals[2].is_end_feature


def test_mixed_native_curve_is_segmented_into_longitudinal_and_end_chains() -> None:
    entities, polygon = _mixed_curve_outline()

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=50.0)

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"
    assert tuple(interval.index for interval in proof.intervals) == (0, 1)
    assert not proof.intervals[0].is_end_feature
    assert proof.intervals[1].is_end_feature
    assert 0 in proof.intervals[0].upper_entity_indices
    assert 0 in proof.intervals[1].upper_entity_indices
    assert 0 in proof.intervals[1].lower_entity_indices


def test_mixed_native_curve_topology_is_independent_of_entity_splits() -> None:
    entities, polygon = _split_mixed_curve_outline()

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=50.0)

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"
    assert tuple(interval.index for interval in proof.intervals) == (0, 1)
    assert not proof.intervals[0].is_end_feature
    assert proof.intervals[1].is_end_feature
    assert 0 in proof.intervals[1].lower_entity_indices
    assert 1 in proof.intervals[1].upper_entity_indices


@pytest.mark.parametrize("curve_kind", ("ARC", "ELLIPSE"))
def test_smooth_same_side_curve_course_is_native_split_invariant(
    curve_kind: str,
) -> None:
    unsplit_entities, unsplit_polygon = _smooth_curve_course_outline(
        curve_kind,
        split=False,
    )
    split_entities, split_polygon = _smooth_curve_course_outline(
        curve_kind,
        split=True,
    )

    unsplit = analyze_longitudinal_outline(
        unsplit_entities,
        unsplit_polygon,
        thickness_mm=30.0,
    )
    split = analyze_longitudinal_outline(
        split_entities,
        split_polygon,
        thickness_mm=30.0,
    )

    assert len(unsplit.intervals) == len(split.intervals) == 1
    assert unsplit.carrier_interval_indices == split.carrier_interval_indices == (0,)
    assert unsplit.selection_reason == split.selection_reason == "unique_longest_body"
    unsplit_interval = unsplit.intervals[0]
    split_interval = split.intervals[0]
    assert split_interval.left_station.upper_x_mm == pytest.approx(
        unsplit_interval.left_station.upper_x_mm
    )
    assert split_interval.left_station.lower_x_mm == pytest.approx(
        unsplit_interval.left_station.lower_x_mm
    )
    assert split_interval.right_station.upper_x_mm == pytest.approx(
        unsplit_interval.right_station.upper_x_mm
    )
    assert split_interval.right_station.lower_x_mm == pytest.approx(
        unsplit_interval.right_station.lower_x_mm
    )
    assert split_interval.upper_span_mm == pytest.approx(unsplit_interval.upper_span_mm)
    assert split_interval.lower_span_mm == pytest.approx(unsplit_interval.lower_span_mm)
    assert split_interval.upper_delta_y_mm == pytest.approx(unsplit_interval.upper_delta_y_mm)
    assert split_interval.lower_delta_y_mm == pytest.approx(unsplit_interval.lower_delta_y_mm)
    assert split_interval.is_end_feature is unsplit_interval.is_end_feature
    assert split_interval.is_turn_candidate is unsplit_interval.is_turn_candidate
    assert set(split_interval.upper_entity_indices) >= {0, 1}


def test_station_band_just_over_strict_limit_is_rejected() -> None:
    entities, polygon = _paired_outline(
        ((0.0, 100.0), (400.0, 100.0), (800.0, 80.0)),
        ((0.0, 0.0), (430.10000001, 0.0), (800.0, 20.0)),
    )

    with pytest.raises(PLSplitError) as error:
        analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert error.value.code == "STATION_BAND_TOO_WIDE"


def test_contained_native_boundary_fragment_does_not_create_a_false_branch() -> None:
    document = ezdxf.new()
    modelspace = document.modelspace()
    entities = tuple(
        modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in (
            ((0.0, 100.0), (800.0, 100.0)),
            ((800.0, 100.0), (800.0, 0.0)),
            ((800.0, 0.0), (0.0, 0.0)),
            ((0.0, 0.0), (0.0, 100.0)),
            ((0.0, 100.0), (300.0, 100.0)),
        )
    )

    proof = analyze_longitudinal_outline(
        entities,
        Polygon(((0.0, 0.0), (800.0, 0.0), (800.0, 100.0), (0.0, 100.0))),
        thickness_mm=30.0,
    )

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"
    assert tuple(interval.index for interval in proof.intervals) == (0,)

    transformed, _ = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=800.0,
        k_length_mm=820.0,
        bom_length_mm=810.0,
        anchor_x_mm=0.0,
    )

    assert len(transformed) == 4
    assert validate_closed_outline(transformed).area == pytest.approx(82_000.0)


def test_reentrant_end_chain_ledge_stays_out_of_longitudinal_courses() -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 240.0),
            (450.0, 240.0),
            (700.0, 200.0),
            (960.0, 200.0),
            (960.0, 40.0),
            (700.0, 40.0),
            (450.0, 0.0),
            (90.0, 0.0),
            (130.0, 90.0),
            (0.0, 140.0),
        )
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (3,)
    assert proof.selection_reason == "paired_visible_turn"
    assert tuple(interval.index for interval in proof.intervals) == (0, 1, 2, 3, 4)


def test_shared_tip_topology_proves_pointed_end_stations_are_independent() -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 175.0),
            (170.0, 300.0),
            (600.0, 300.0),
            (900.0, 260.0),
            (1300.0, 260.0),
            (1300.0, 40.0),
            (900.0, 40.0),
            (600.0, 0.0),
            (30.0, 0.0),
        )
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=35.0)

    stations = (
        *(interval.left_station for interval in proof.intervals),
        proof.intervals[-1].right_station,
    )
    assert tuple((station.upper_x_mm, station.lower_x_mm) for station in stations) == (
        (0.0, 0.0),
        (30.0, 30.0),
        (170.0, 170.0),
        (600.0, 600.0),
        (900.0, 900.0),
        (1300.0, 1300.0),
    )
    assert proof.carrier_interval_indices == (3,)
    assert proof.selection_reason == "paired_visible_turn"


def test_near_coincident_curves_are_not_approximately_deduplicated() -> None:
    entities, _ = _mixed_curve_outline()
    duplicate = entities[0].copy()
    duplicate.dxf.center = entities[0].dxf.center + (0.0, 0.0005, 0.0)

    with pytest.raises(PLSplitError) as error:
        canonical_boundary_pieces((*entities, duplicate))

    assert error.value.code == "LONGITUDINAL_TOPOLOGY"


def test_exact_duplicate_native_curve_is_deduplicated() -> None:
    entities, polygon = _mixed_curve_outline()
    duplicate = entities[0].copy()

    pieces = canonical_boundary_pieces((*entities, duplicate))

    assert sum(piece.entity.dxftype() == "ELLIPSE" for piece in pieces) == 1
    proof = analyze_longitudinal_outline(
        (*entities, duplicate),
        polygon,
        thickness_mm=50.0,
    )
    assert proof.carrier_interval_indices == (0,)


def test_native_chamfered_end_chain_projects_its_own_station_events() -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 220.0),
            (15.0, 235.0),
            (694.4, 235.0),
            (709.4, 220.0),
            (709.4, 0.0),
            (0.0, 0.0),
        )
    )

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=14.0)

    stations = (
        *(interval.left_station for interval in proof.intervals),
        proof.intervals[-1].right_station,
    )
    assert tuple((station.upper_x_mm, station.lower_x_mm) for station in stations) == (
        (0.0, 0.0),
        (15.0, 15.0),
        (694.4, 694.4),
        (709.4, 709.4),
    )
    assert proof.carrier_interval_indices == (1,)


def test_single_interval_triangle_is_its_own_carrier() -> None:
    entities, polygon = _line_outline(((0.0, 500.0), (0.0, 0.0), (531.315, 0.0)))

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert tuple(interval.index for interval in proof.intervals) == (0,)
    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"


def test_mixed_curve_and_overlapping_lines_preserve_native_provenance(
    tmp_path: Path,
) -> None:
    entities, polygon = _mixed_arc_overlapping_line_outline()

    pieces = canonical_boundary_pieces(entities)
    curve_piece = next(piece for piece in pieces if piece.entity.dxftype() == "ELLIPSE")
    assert curve_piece.source_index == 0
    assert curve_piece.source_handle == str(entities[0].dxf.handle)
    assert curve_piece.is_noded_piece is False
    assert {piece.source_index for piece in pieces if piece.entity.dxftype() == "LINE"} == {
        1,
        2,
        3,
    }
    assert all(piece.is_noded_piece for piece in pieces if piece.entity.dxftype() == "LINE")
    proved = validate_closed_outline(
        tuple(piece.entity for piece in pieces),
        tolerance_mm=1e-7,
    )
    assert proved.symmetric_difference(polygon).area <= 0.000001

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=50.0)
    assert proof.carrier_interval_indices == (0,)
    assert str(entities[0].dxf.handle) in proof.intervals[0].source_handles
    min_x, min_y, max_x, max_y = polygon.bounds
    projection = max_x - min_x
    transformed, metrics = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=projection,
        k_length_mm=projection,
        bom_length_mm=projection,
        anchor_x_mm=min_x,
    )
    assert {entity.dxftype() for entity in transformed} == {"ELLIPSE", "LINE"}

    from steel_dxf_split.pl.writer import write_pl_dxf

    developed = DevelopedPlate(
        metadata=PLMetadata(
            "anonymous-mixed-native",
            50.0,
            max_y - min_y,
            projection,
        ),
        outline=PlateOutline(
            outer_entities=entities,
            polygon=polygon,
            projection_length_mm=projection,
            width_mm=max_y - min_y,
            anchor_x_mm=min_x,
            source_handles=tuple(str(entity.dxf.handle) for entity in entities),
            candidate_count=1,
        ),
        section=SectionProof(
            polygon=Polygon(
                (
                    (0.0, -50.0),
                    (projection, -50.0),
                    (projection, 0.0),
                    (0.0, 0.0),
                )
            ),
            k_length_mm=projection,
            equivalent_surface_lengths_mm=(projection, projection),
            proof_method="section_area_over_thickness_k_half",
            source_handles=(),
            candidate_count=1,
        ),
        longitudinal=proof,
        transformed_entities=transformed,
        metrics=metrics,
    )
    output = tmp_path / "mixed-native.dxf"
    write_pl_dxf(developed, output)
    saved = ezdxf.readfile(output)
    saved_types = {
        entity.dxftype() for entity in saved.modelspace() if entity.dxf.layer == "PLATE_CUT"
    }
    assert saved_types == {"ELLIPSE", "LINE"}
    assert saved.audit().has_errors is False


def test_invalid_mixed_native_candidates_fail_closed() -> None:
    entities, _ = _mixed_arc_overlapping_line_outline()
    dangling = entities[1].copy()
    dangling.dxf.start = (400.0, 0.0)
    dangling.dxf.end = (400.0, 100.0)

    with pytest.raises(PLSplitError) as error:
        canonical_boundary_pieces((*entities, dangling))

    assert error.value.code == "LONGITUDINAL_TOPOLOGY"


def test_overlapping_collinear_boundary_fragments_are_canonicalized(
    tmp_path: Path,
) -> None:
    document = ezdxf.new()
    modelspace = document.modelspace()
    entities = tuple(
        modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in (
            ((0.0, 100.0), (600.0, 100.0)),
            ((400.0, 100.0), (800.0, 100.0)),
            ((800.0, 100.0), (800.0, 0.0)),
            ((800.0, 0.0), (0.0, 0.0)),
            ((0.0, 0.0), (0.0, 100.0)),
        )
    )
    polygon = validate_closed_outline(entities, tolerance_mm=0.1)

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"
    assert tuple(interval.index for interval in proof.intervals) == (0,)
    assert set(proof.intervals[0].upper_entity_indices) == {0, 1}
    assert set(proof.intervals[0].lower_entity_indices) == {3}
    assert set(proof.intervals[0].source_handles) == {
        str(entities[index].dxf.handle) for index in (0, 1, 3)
    }

    transformed, metrics = transform_outline(
        entities,
        longitudinal=proof,
        projection_length_mm=800.0,
        k_length_mm=820.0,
        bom_length_mm=810.0,
        anchor_x_mm=0.0,
    )

    assert validate_closed_outline(transformed).area == pytest.approx(82_000.0)

    from steel_dxf_split.pl.writer import write_pl_dxf

    developed = DevelopedPlate(
        metadata=PLMetadata("anonymous-overlap", 30.0, 100.0, 810.0),
        outline=PlateOutline(
            outer_entities=entities,
            polygon=polygon,
            projection_length_mm=800.0,
            width_mm=100.0,
            anchor_x_mm=0.0,
            source_handles=(),
            candidate_count=1,
        ),
        section=SectionProof(
            polygon=Polygon(((0.0, -30.0), (820.0, -30.0), (820.0, 0.0), (0.0, 0.0))),
            k_length_mm=820.0,
            equivalent_surface_lengths_mm=(820.0, 820.0),
            proof_method="section_area_over_thickness_k_half",
            source_handles=(),
            candidate_count=1,
        ),
        longitudinal=proof,
        transformed_entities=transformed,
        metrics=metrics,
    )
    output = tmp_path / "overlap.dxf"
    write_pl_dxf(developed, output)
    saved = ezdxf.readfile(output)
    saved_types = tuple(
        entity.dxftype() for entity in saved.modelspace() if entity.dxf.layer == "PLATE_CUT"
    )
    assert saved_types
    assert set(saved_types) == {"LINE"}


def test_sub_tolerance_line_wobble_does_not_create_turn_stations() -> None:
    document = ezdxf.new()
    modelspace = document.modelspace()
    entities = tuple(
        modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in (
            ((0.0, 100.0), (150.0, 100.03)),
            ((100.0, 100.02), (200.0, 100.04)),
            ((200.0, 100.04), (400.0, 100.0)),
            ((400.0, 100.0), (800.0, 100.05)),
            ((800.0, 100.05), (800.0, 0.0)),
            ((800.0, 0.0), (300.0, 0.03)),
            ((300.0, 0.03), (0.0, 0.0)),
            ((0.0, 0.0), (0.0, 100.0)),
        )
    )
    polygon = validate_closed_outline(entities, tolerance_mm=0.1)

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    assert proof.carrier_interval_indices == (0,)
    assert proof.selection_reason == "unique_longest_body"
    assert tuple(interval.index for interval in proof.intervals) == (0,)


def test_competing_overlap_cycles_select_the_proven_native_boundary() -> None:
    document = ezdxf.new()
    modelspace = document.modelspace()
    entities = tuple(
        modelspace.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in (
            ((0.0, 100.0), (600.0, 100.0)),
            ((600.0, 100.0), (800.0, 100.05)),
            ((800.0, 100.05), (800.0, 0.0)),
            ((800.0, 0.0), (0.0, 0.0)),
            ((0.0, 0.0), (0.0, 100.0)),
            ((600.0, 100.0), (400.0, 100.05)),
            ((400.0, 100.05), (800.0, 100.05)),
        )
    )
    material = validate_closed_outline(entities, tolerance_mm=0.1)

    pieces = canonical_boundary_pieces(entities)
    proved = validate_closed_outline(
        tuple(piece.entity for piece in pieces),
        tolerance_mm=1e-7,
    )

    assert proved.symmetric_difference(material).area <= 0.000001
    assert {piece.source_index for piece in pieces} == {0, 2, 3, 4, 5, 6}
    assert all(piece.entity.dxftype() == "LINE" for piece in pieces)
    assert all(piece.is_noded_piece for piece in pieces)


def test_three_end_near_events_cannot_bypass_a_shared_wide_station() -> None:
    entities, polygon = _line_outline(
        (
            (0.0, 100.0),
            (90.0, 100.0),
            (100.0, 80.0),
            (100.0, 20.0),
            (10.0, 0.0),
            (0.0, 0.0),
        )
    )

    with pytest.raises(PLSplitError) as error:
        analyze_longitudinal_outline(entities, polygon, thickness_mm=15.0)

    assert error.value.code == "STATION_BAND_TOO_WIDE"
