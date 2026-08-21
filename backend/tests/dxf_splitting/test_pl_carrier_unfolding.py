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
from steel_dxf_split.pl.development import calculate_target, ceil_tenth_mm, transform_outline
from steel_dxf_split.pl.geometry import flatten_entity, validate_closed_outline
from steel_dxf_split.pl.longitudinal import (
    analyze_longitudinal_outline,
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
    assert len(transformed) == len(entities) + 2
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
    assert len(saved_plate_entities) > len(source_entities)
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
    tampered = []
    for entity in developed.transformed_entities:
        clone = entity.copy()
        if clone.dxftype() == "LINE":
            for attribute in ("start", "end"):
                point = clone.dxf.get(attribute)
                if abs(float(point.x) - carrier_right_x) <= 0.001:
                    clone.dxf.set(attribute, (float(point.x) + 5.0, point.y, point.z))
        tampered.append(clone)
    altered = replace(developed, transformed_entities=tuple(tampered))

    with pytest.raises(PLSplitError) as error:
        write_pl_dxf(altered, tmp_path / "shifted-station.dxf")

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


def test_true_wide_pair_stays_paired_beside_one_independent_ledge() -> None:
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

    assert error.value.code == "STATION_BAND_TOO_WIDE"


def test_opposite_independent_ledges_project_instead_of_forming_a_false_pair() -> None:
    entities, polygon = _paired_outline(
        (
            (0.0, 100.0),
            (10.0, 100.0),
            (20.0, 105.0),
            (780.0, 105.0),
            (800.0, 95.0),
        ),
        (
            (0.0, 0.0),
            (10.0, 5.0),
            (770.0, 5.0),
            (780.0, 0.0),
            (800.0, 0.0),
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
        (20.0, 20.0),
        (770.0, 770.0),
        (780.0, 780.0),
        (800.0, 800.0),
    )
    assert proof.carrier_interval_indices == (2,)
    assert proof.selection_reason == "unique_longest_body"


def test_near_opposite_independent_ledges_project_above_station_limit() -> None:
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

    proof = analyze_longitudinal_outline(entities, polygon, thickness_mm=30.0)

    stations = (
        *(interval.left_station for interval in proof.intervals),
        proof.intervals[-1].right_station,
    )
    assert tuple((station.upper_x_mm, station.lower_x_mm) for station in stations) == (
        (0.0, 0.0),
        (10.0, 10.0),
        (20.0, 20.0),
        (60.0, 60.0),
        (70.0, 70.0),
        (80.0, 80.0),
    )
    assert proof.carrier_interval_indices == (2,)
    assert proof.selection_reason == "unique_longest_body"


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
