from __future__ import annotations

from collections.abc import Callable
from math import cos, radians, sin

import ezdxf
import pytest
from shapely.affinity import rotate
from shapely.geometry import Polygon

from steel_dxf_split.bh_geometry import (
    ProjectionAnnotationMask,
    _clean_candidate_polygon,
    _reconstruct_proven_rectangular_projection,
    _regularize_micro_topology,
    entities_bbox,
    select_flange_polygons,
)
from steel_dxf_split.bh_projection_semantics import (
    ProjectionEdgeAuthority,
    analyse_projection_boundary,
    evaluate_boundary_repair,
)


Point = tuple[float, float]
Transform = Callable[[Point], Point]


def _identity(point: Point) -> Point:
    return point


def _translate(point: Point) -> Point:
    return point[0] + 37.25, point[1] - 91.5


def _mirror_x(point: Point) -> Point:
    return -point[0], point[1]


def _mirror_y(point: Point) -> Point:
    return point[0], -point[1]


def _swap_axes(point: Point) -> Point:
    return point[1], point[0]


def _short_member(point: Point) -> Point:
    return 0.7 * point[0], point[1]


def _long_member(point: Point) -> Point:
    return 1.7 * point[0], point[1]


def _source_polygon(
    points: list[Point],
    *,
    transform: Transform = _identity,
) -> tuple[Polygon, list[object], tuple[str, ...]]:
    transformed = [transform(point) for point in points]
    document = ezdxf.new()
    document.layers.add("Part")
    layout = document.modelspace()
    entities = [
        layout.add_line(start, end, dxfattribs={"layer": "Part"})
        for start, end in zip(
            transformed,
            transformed[1:] + transformed[:1],
            strict=True,
        )
    ]
    source_ids = tuple(f"source:edge:{index}" for index in range(len(entities)))
    return Polygon(transformed), entities, source_ids


@pytest.mark.parametrize("transform", [_identity, _translate, _mirror_x, _mirror_y])
def test_projection_boundary_keeps_all_direct_source_edges_under_rigid_transforms(
    transform: Transform,
) -> None:
    polygon, entities, source_ids = _source_polygon(
        [(0.0, 0.0), (1000.0, 0.0002), (1000.0, 200.0), (0.0, 200.0)],
        transform=transform,
    )

    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=source_ids,
        association_tolerance_mm=0.001,
    )

    assert len(semantics.direct_edges) == 4
    assert {edge.authority for edge in semantics.direct_edges} == {
        ProjectionEdgeAuthority.DIRECT
    }
    assert {
        source_id
        for edge in semantics.direct_edges
        for source_id in edge.source_ids
    } == set(source_ids)


def test_boundary_repair_reports_a_source_backed_bevel_as_lost() -> None:
    original, entities, source_ids = _source_polygon(
        [(2.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0), (0.0, 2.0)]
    )
    semantics = analyse_projection_boundary(
        original,
        entities,
        entity_source_ids=source_ids,
        association_tolerance_mm=0.01,
    )
    rectangle = Polygon([(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0)])

    decision = evaluate_boundary_repair(
        original,
        rectangle,
        semantics,
        fidelity_tolerance_mm=1e-7,
        repair_kind="test_rectangle",
    )

    assert decision.applied is False
    assert decision.polygon.equals_exact(original, 0.0)
    assert decision.reason == "direct_source_edge_loss"
    assert decision.lost_source_ids == (source_ids[-1],)
    assert decision.to_dict()["protected_source_ids"] == list(source_ids)


def test_boundary_repair_accepts_a_source_backed_true_rectangle() -> None:
    original, entities, source_ids = _source_polygon(
        [(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0)]
    )
    semantics = analyse_projection_boundary(
        original,
        entities,
        entity_source_ids=source_ids,
    )
    candidate = Polygon([(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0)])

    decision = evaluate_boundary_repair(
        original,
        candidate,
        semantics,
        fidelity_tolerance_mm=1e-7,
        repair_kind="test_rectangle",
    )

    assert decision.applied is True
    assert decision.lost_source_ids == ()
    assert decision.polygon.equals_exact(candidate, 0.0)


@pytest.mark.parametrize("bevel", [10.0, 2.0, 0.0002])
def test_proven_rectangle_reconstruction_never_erases_a_source_backed_bevel(
    bevel: float,
) -> None:
    polygon, entities, source_ids = _source_polygon(
        [
            (bevel, 0.0),
            (1000.0, 0.0),
            (1000.0, 200.0),
            (0.0, 200.0),
            (0.0, bevel),
        ]
    )

    decision = _reconstruct_proven_rectangular_projection(
        polygon,
        entities,
        entity_source_ids=source_ids,
    )

    assert decision.applied is False
    assert decision.reason == "direct_source_edge_loss"
    assert decision.polygon.equals_exact(polygon, 0.0)
    assert source_ids[-1] in decision.lost_source_ids


def test_proven_rectangle_reconstruction_does_not_axis_align_a_rotated_view() -> None:
    base, _, _ = _source_polygon(
        [(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0)]
    )
    rotated = rotate(base, 0.001, origin=(0.0, 0.0), use_radians=False)
    points = [(float(x), float(y)) for x, y in list(rotated.exterior.coords)[:-1]]
    polygon, entities, source_ids = _source_polygon(points)

    decision = _reconstruct_proven_rectangular_projection(
        polygon,
        entities,
        entity_source_ids=source_ids,
    )

    assert decision.applied is False
    assert decision.polygon.equals_exact(polygon, 0.0)


def test_proven_rectangle_reconstruction_can_remove_an_unsupported_noding_sliver() -> None:
    _, entities, source_ids = _source_polygon(
        [(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0)]
    )
    polygon = Polygon(
        [
            (0.0, 0.0),
            (1000.0, 0.0),
            (1000.0, 200.0),
            (0.0, 200.0),
            (0.0, 100.01),
            (0.01, 100.0),
            (0.0, 99.99),
        ]
    )

    decision = _reconstruct_proven_rectangular_projection(
        polygon,
        entities,
        entity_source_ids=source_ids,
    )

    assert decision.applied is True
    assert decision.reason == "source_edges_conserved"
    assert list(decision.polygon.exterior.coords)[:-1] == [
        (0.0, 0.0),
        (1000.0, 0.0),
        (1000.0, 200.0),
        (0.0, 200.0),
    ]


def test_flange_selection_path_retains_a_source_backed_bevel() -> None:
    source_polygon, entities, source_ids = _source_polygon(
        [(2.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0), (0.0, 2.0)]
    )

    polygons, _, diagnostics = select_flange_polygons(
        entities,
        entity_source_ids=source_ids,
        flange_width=200.0,
        nominal_length=1000.0,
        source_bbox=entities_bbox(entities),
    )

    assert len(polygons) == 1
    assert polygons[0].hausdorff_distance(source_polygon) <= 0.001
    assert len(list(polygons[0].exterior.coords)) - 1 == 5
    repairs = diagnostics["projection_boundary_repairs"]
    assert any(
        repair["reason"] == "direct_source_edge_loss"
        and source_ids[-1] in repair["lost_source_ids"]
        for repair in repairs
    )
    conservation = diagnostics["projection_boundary_conservation"]
    assert len(conservation) == 1
    assert conservation[0]["lost_source_ids"] == []
    assert set(conservation[0]["protected_source_ids"]) == set(source_ids)


def _annotation_masked_flange(
    *,
    second_fragment_y: float = 0.0,
    include_text: bool = True,
) -> tuple[list[object], tuple[str, ...], ProjectionAnnotationMask]:
    document = ezdxf.new()
    document.layers.add("Part")
    document.layers.add("BoltMark")
    layout = document.modelspace()
    entities = [
        layout.add_line((0.0, 0.0), (120.0, 0.0), dxfattribs={"layer": "Part"}),
        layout.add_line(
            (340.0, second_fragment_y),
            (1000.0, second_fragment_y),
            dxfattribs={"layer": "Part"},
        ),
        layout.add_line(
            (1000.0, second_fragment_y),
            (1000.0, 200.0),
            dxfattribs={"layer": "Part"},
        ),
        layout.add_line((1000.0, 200.0), (0.0, 200.0), dxfattribs={"layer": "Part"}),
        layout.add_line((0.0, 200.0), (0.0, 0.0), dxfattribs={"layer": "Part"}),
    ]
    annotation_entities: list[object] = []
    if include_text:
        annotation_entities.append(
            layout.add_text(
                "6Φ22",
                height=60.0,
                dxfattribs={"layer": "BoltMark", "insert": (130.0, -65.0)},
            )
        )
    annotation_entities.append(
        layout.add_line(
            (115.0, -77.0),
            (345.0, -77.0),
            dxfattribs={"layer": "BoltMark"},
        )
    )
    source_ids = tuple(f"source:edge:{index}" for index in range(len(entities)))
    mask = ProjectionAnnotationMask(
        semantic_layer="BoltMark",
        entities=tuple(annotation_entities),
        source_ids=tuple(
            f"source:annotation:{index}" for index in range(len(annotation_entities))
        ),
    )
    return entities, source_ids, mask


def test_flange_selection_recovers_only_an_explicit_annotation_masked_gap() -> None:
    entities, source_ids, mask = _annotation_masked_flange()

    polygons, _, diagnostics = select_flange_polygons(
        entities,
        entity_source_ids=source_ids,
        flange_width=200.0,
        nominal_length=1000.0,
        source_bbox=entities_bbox(entities),
        annotation_masks=(mask,),
    )

    assert len(polygons) == 1
    assert polygons[0].hausdorff_distance(
        Polygon([(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0)])
    ) <= 0.001
    assert diagnostics["annotation_masked_projection_gaps"] == [
        {
            "repair_kind": "annotation_masked_projection_gap",
            "semantic_layer": "BoltMark",
            "annotation_source_ids": [
                "source:annotation:0",
                "source:annotation:1",
            ],
            "gap_length_mm": 220.0,
            "bridge": [[120.0, 0.0], [340.0, 0.0]],
        }
    ]


def test_flange_selection_does_not_close_an_unexplained_projection_gap() -> None:
    entities, source_ids, _ = _annotation_masked_flange()

    with pytest.raises(ValueError, match="full-width flange"):
        select_flange_polygons(
            entities,
            entity_source_ids=source_ids,
            flange_width=200.0,
            nominal_length=1000.0,
            source_bbox=entities_bbox(entities),
        )


@pytest.mark.parametrize(
    ("second_fragment_y", "include_text"),
    [(0.2, True), (0.0, False)],
)
def test_flange_selection_rejects_weak_annotation_gap_evidence(
    second_fragment_y: float,
    include_text: bool,
) -> None:
    entities, source_ids, mask = _annotation_masked_flange(
        second_fragment_y=second_fragment_y,
        include_text=include_text,
    )

    with pytest.raises(ValueError, match="full-width flange"):
        select_flange_polygons(
            entities,
            entity_source_ids=source_ids,
            flange_width=200.0,
            nominal_length=1000.0,
            source_bbox=entities_bbox(entities),
            annotation_masks=(mask,),
        )


def test_polygon_simplification_is_rejected_when_it_loses_a_shallow_direct_edge() -> None:
    polygon, entities, source_ids = _source_polygon(
        [
            (0.0, 0.0),
            (500.0, 0.0002),
            (1000.0, 0.0),
            (1000.0, 200.0),
            (0.0, 200.0),
        ]
    )
    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=source_ids,
        association_tolerance_mm=0.01,
    )
    diagnostics: list[dict[str, object]] = []

    cleaned = _clean_candidate_polygon(
        polygon,
        grid_size=0.001,
        projection_semantics=semantics,
        repair_diagnostics=diagnostics,
    )

    assert cleaned.equals_exact(polygon, 0.0)
    assert diagnostics[-1]["applied"] is False
    assert diagnostics[-1]["reason"] == "direct_source_edge_loss"


def test_micro_topology_regularization_is_rejected_when_it_erases_a_direct_bevel() -> None:
    polygon, entities, source_ids = _source_polygon(
        [(0.1, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0), (0.0, 0.1)]
    )
    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=source_ids,
    )

    regularized, diagnostics = _regularize_micro_topology(
        polygon,
        projection_semantics=semantics,
    )

    assert regularized.equals_exact(polygon, 0.0)
    assert diagnostics["applied"] is False
    assert diagnostics["reason"] == "direct_source_edge_loss"


@pytest.mark.parametrize(
    ("transform", "long_axis"),
    [
        (_identity, "x"),
        (_translate, "x"),
        (_mirror_x, "x"),
        (_mirror_y, "x"),
        (_swap_axes, "y"),
        (_short_member, "x"),
        (_long_member, "x"),
    ],
)
def test_longitudinal_projection_overlay_rule_is_geometry_invariant(
    transform: Transform,
    long_axis: str,
) -> None:
    polygon, entities, source_ids = _source_polygon(
        [
            (10.0, 50.0),
            (0.0, 50.0),
            (0.0, 50.2),
            (10000.0, 50.2),
            (10000.0, 0.0),
            (0.0, 0.0),
            (0.0, 0.2),
            (10.0, 0.2),
            (20.0, 10.0),
            (0.0, 15.0),
            (0.0, 35.0),
            (20.0, 40.0),
        ],
        transform=transform,
    )
    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=source_ids,
    )

    regularized, diagnostics = _regularize_micro_topology(
        polygon,
        projection_semantics=semantics,
        long_axis=long_axis,
    )

    assert diagnostics["applied"] is True
    assert diagnostics["reason"] == "longitudinal_projection_overlay_regularized"
    assert diagnostics["reclassified_source_ids"]
    assert not (
        set(diagnostics["reclassified_source_ids"])
        & set(diagnostics["protected_source_ids"])
    )
    assert regularized.hausdorff_distance(polygon) > 1.0


@pytest.mark.parametrize(
    ("member_length", "return_length", "separation"),
    [
        (2200.0, 6.0, 0.08),
        (10000.0, 12.0, 0.25),
        (18000.0, 25.0, 0.40),
    ],
)
def test_projection_overlay_rule_generalizes_across_member_and_overlay_sizes(
    member_length: float,
    return_length: float,
    separation: float,
) -> None:
    polygon, entities, source_ids = _source_polygon(
        [
            (return_length, 50.0),
            (0.0, 50.0),
            (0.0, 50.0 + separation),
            (member_length, 50.0 + separation),
            (member_length, 0.0),
            (0.0, 0.0),
            (0.0, separation),
            (return_length, separation),
            (2.0 * return_length, 10.0),
            (0.0, 15.0),
            (0.0, 35.0),
            (2.0 * return_length, 40.0),
        ]
    )
    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=source_ids,
    )

    _, diagnostics = _regularize_micro_topology(
        polygon,
        projection_semantics=semantics,
        long_axis="x",
    )

    assert diagnostics["applied"] is True
    assert diagnostics["reason"] == "longitudinal_projection_overlay_regularized"


def test_local_longitudinal_notch_without_a_continuing_course_is_not_overlay() -> None:
    polygon, entities, source_ids = _source_polygon(
        [
            (0.0, 0.0),
            (10000.0, 0.0),
            (10000.0, 200.0),
            (0.0, 200.0),
            (0.0, 100.2),
            (20.0, 100.2),
            (20.0, 100.0),
            (0.0, 100.0),
        ]
    )
    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=source_ids,
    )

    regularized, diagnostics = _regularize_micro_topology(
        polygon,
        projection_semantics=semantics,
        long_axis="x",
    )

    assert regularized.equals_exact(polygon, 0.0)
    assert diagnostics["applied"] is False
    assert diagnostics["reason"] == "direct_source_edge_loss"


def test_projection_overlay_rule_does_not_mask_an_unrelated_direct_bevel() -> None:
    polygon, entities, source_ids = _source_polygon(
        [
            (10.0, 50.0),
            (0.0, 50.0),
            (0.0, 50.2),
            (10000.0, 50.2),
            (10000.0, 0.1),
            (9999.9, 0.0),
            (0.0, 0.0),
            (0.0, 0.2),
            (10.0, 0.2),
            (20.0, 10.0),
            (0.0, 15.0),
            (0.0, 35.0),
            (20.0, 40.0),
        ]
    )
    semantics = analyse_projection_boundary(
        polygon,
        entities,
        entity_source_ids=source_ids,
    )

    regularized, diagnostics = _regularize_micro_topology(
        polygon,
        projection_semantics=semantics,
        long_axis="x",
    )

    assert regularized.equals_exact(polygon, 0.0)
    assert diagnostics["applied"] is False
    assert diagnostics["reason"] == "direct_source_edge_loss"


def test_subgrid_line_far_from_selected_projection_is_not_boundary_evidence() -> None:
    polygon, entities, source_ids = _source_polygon(
        [(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0)]
    )
    far_line = entities[0].doc.modelspace().add_line(
        (5000.0, 5000.0),
        (5000.0001, 5000.0),
        dxfattribs={"layer": "Part"},
    )

    semantics = analyse_projection_boundary(
        polygon,
        [*entities, far_line],
        entity_source_ids=(*source_ids, "far-degenerate-line"),
        association_tolerance_mm=0.001,
    )

    assert "far-degenerate-line" not in semantics.protected_source_ids


def test_parallel_edge_from_overlapping_projection_is_not_owned_by_selected_boundary() -> None:
    polygon, entities, source_ids = _source_polygon(
        [(0.0, 0.0), (1000.0, 0.0), (1000.0, 200.0), (0.0, 200.0)]
    )
    neighbouring_edge = entities[0].doc.modelspace().add_line(
        (0.01, 0.0),
        (0.01, 200.0),
        dxfattribs={"layer": "Part"},
    )

    semantics = analyse_projection_boundary(
        polygon,
        [*entities, neighbouring_edge],
        entity_source_ids=(*source_ids, "neighbouring-projection-edge"),
        association_tolerance_mm=0.001,
    )

    assert "neighbouring-projection-edge" not in semantics.protected_source_ids


def test_short_line_on_source_arc_circle_is_a_transition_chord_not_a_straight_cut_edge() -> None:
    document = ezdxf.new()
    document.layers.add("Part")
    document.linetypes.add("XKITLINE04", pattern=[0.2, 0.1, -0.1])
    layout = document.modelspace()
    radius = 10.0
    chord_start = (radius * cos(radians(72.0)), radius * sin(radians(72.0)))
    chord_end = (0.0, radius)
    chord = layout.add_line(chord_start, chord_end, dxfattribs={"layer": "Part"})
    hidden_arc = layout.add_arc(
        (0.0, 0.0),
        radius,
        18.0,
        72.0,
        dxfattribs={"layer": "Part", "linetype": "XKITLINE04"},
    )
    polygon = Polygon([chord_start, chord_end, (0.0, 20.0), (20.0, 20.0), (20.0, 0.0)])

    semantics = analyse_projection_boundary(
        polygon,
        [chord, hidden_arc],
        entity_source_ids=("transition-chord", "hidden-arc"),
        association_tolerance_mm=0.001,
    )

    assert "transition-chord" not in semantics.protected_source_ids


def test_unconnected_chord_on_the_same_circle_remains_a_direct_edge() -> None:
    document = ezdxf.new()
    document.layers.add("Part")
    layout = document.modelspace()
    radius = 10.0
    chord_start = (0.0, radius)
    chord_end = (radius * cos(radians(108.0)), radius * sin(radians(108.0)))
    chord = layout.add_line(chord_start, chord_end, dxfattribs={"layer": "Part"})
    unrelated_arc = layout.add_arc(
        (0.0, 0.0),
        radius,
        18.0,
        72.0,
        dxfattribs={"layer": "Part"},
    )
    polygon = Polygon(
        [chord_start, chord_end, (-20.0, 20.0), (20.0, 20.0), (20.0, 0.0)]
    )

    semantics = analyse_projection_boundary(
        polygon,
        [chord, unrelated_arc],
        entity_source_ids=("fabrication-chord", "unrelated-arc"),
        association_tolerance_mm=0.001,
    )

    assert "fabrication-chord" in semantics.protected_source_ids
