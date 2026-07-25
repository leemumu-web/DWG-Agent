from __future__ import annotations

from math import cos, radians, sin

import ezdxf
from ezdxf.math import Matrix44

from bh_transform_fixtures import transform_modelspace

from steel_dxf_split.bh_associations import (
    AssociationStrength,
    DrawingEdgeKind,
    DrawingNodeKind,
    build_drawing_graph,
)
from steel_dxf_split.bh_annotations import extract_annotation_model
from steel_dxf_split.bh_frames import infer_member_frames
from steel_dxf_split.bh_regions import build_view_regions, materialize_lowering_ir
from steel_dxf_split.bh_semantics import parse_bh_metadata_ir
from steel_dxf_split.bh_source import decode_source_document


def _base_document():
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    for layer in ("Part", "Z-DIMENSIONS", "PartMark", "Section"):
        doc.layers.add(layer)
    doc.modelspace().add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 300), (0, 300)],
        close=True,
        dxfattribs={"layer": "Part"},
    )
    return doc


def _graph(doc):
    source = decode_source_document(doc)
    frame = infer_member_frames(source).selected
    regions = build_view_regions(source, frame)
    return build_drawing_graph(source, regions, frame)


def _add_exploded_dimension(
    doc,
    *,
    include_extensions: bool = True,
    duplicate_dimension_line: bool = False,
    reverse_entity_order: bool = False,
) -> None:
    attrs = {"layer": "Z-DIMENSIONS"}
    facts: list[tuple[str, object]] = [
        ("line", ((0, 400), (1000, 400))),
        ("text", ("1000", (480, 420))),
    ]
    if include_extensions:
        facts.extend(
            [
                ("line", ((0, 0), (0, 420))),
                ("line", ((1000, 0), (1000, 420))),
            ]
        )
    if duplicate_dimension_line:
        facts.append(("line", ((0, 400), (1000, 400))))
    if reverse_entity_order:
        facts.reverse()
    for kind, payload in facts:
        if kind == "line":
            start, end = payload
            doc.modelspace().add_line(start, end, dxfattribs=attrs)
        else:
            value, insert = payload
            doc.modelspace().add_text(
                value,
                dxfattribs={**attrs, "insert": insert, "height": 12.0},
            )


def _measurement_semantic_signature(graph) -> list[tuple[object, ...]]:
    nodes = {node.node_id: node for node in graph.nodes}
    return sorted(
        (
            nodes[edge.source].attributes.get("representation"),
            nodes[edge.source].attributes.get("value_mm"),
            edge.rule_id,
            edge.strength.value,
            round(edge.residual_mm, 6),
            tuple(round(value, 6) for value in nodes[edge.target].attributes["bbox_mm"]),
        )
        for edge in graph.edges_of(DrawingEdgeKind.MEASURES)
    )


def test_explicit_dimension_measures_a_compatible_part_view() -> None:
    doc = _base_document()
    dimension = doc.modelspace().add_linear_dim(
        base=(0, 400),
        p1=(0, 0),
        p2=(1000, 0),
        angle=0,
        dxfattribs={"layer": "Z-DIMENSIONS"},
    )
    dimension.render()

    graph = _graph(doc)
    dimensions = graph.nodes_of(DrawingNodeKind.DIMENSION)
    measurement_edges = graph.edges_of(DrawingEdgeKind.MEASURES)

    assert len(dimensions) == 1
    assert dimensions[0].attributes["representation"] == "explicit"
    assert dimensions[0].attributes["value_mm"] == 1000.0
    assert measurement_edges[0].attributes["property_type"] == "longitudinal_extent"
    assert any(
        edge.source == dimensions[0].node_id
        and edge.strength == AssociationStrength.EXPLICIT
        and edge.residual_mm <= 0.01
        for edge in measurement_edges
    )


def test_dimension_shared_by_overlapping_views_remains_ambiguous() -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    doc.layers.add("Z-DIMENSIONS")
    for block_name in ("VIEW_A", "VIEW_B"):
        block = doc.blocks.new(block_name)
        block.add_lwpolyline(
            [(0, 0), (1000, 0), (1000, 300), (0, 300)],
            close=True,
            dxfattribs={"layer": "Part"},
        )
        doc.modelspace().add_blockref(block_name, (0, 0))
    dimension = doc.modelspace().add_linear_dim(
        base=(0, 400),
        p1=(0, 0),
        p2=(1000, 0),
        angle=0,
        dxfattribs={"layer": "Z-DIMENSIONS"},
    )
    dimension.render()

    graph = _graph(doc)
    model = extract_annotation_model(graph)

    assert len(graph.nodes_of(DrawingNodeKind.VIEW_REGION)) == 2
    assert len(graph.edges_of(DrawingEdgeKind.MEASURES)) == 2
    assert len(model.dimensions) == 2
    assert {item.target_count for item in model.dimensions} == {2}


def test_physical_cut_projects_to_its_owned_view_region() -> None:
    doc = _base_document()
    doc.layers.add("Bolt")
    doc.modelspace().add_circle(
        (250.0, 150.0),
        12.0,
        dxfattribs={"layer": "Bolt"},
    )

    graph = _graph(doc)
    nodes = {node.node_id: node for node in graph.nodes}
    projections = graph.edges_of(DrawingEdgeKind.PROJECTS_TO)

    assert len(projections) == 1
    edge = projections[0]
    assert nodes[edge.source].attributes["semantic_role"] == "physical_cut"
    assert nodes[edge.target].kind == DrawingNodeKind.VIEW_REGION
    assert edge.strength == AssociationStrength.GEOMETRIC


def test_closed_bolt_line_opening_is_a_physical_cut_owned_and_labeled_by_the_view() -> None:
    doc = _base_document()
    doc.layers.add("Bolt")
    doc.layers.add("BoltMark")
    layout = doc.modelspace()
    center = (200.0, 150.0)
    outline = [
        (189.0, 143.5),
        (192.222, 135.722),
        (200.0, 132.5),
        (207.778, 135.722),
        (211.0, 143.5),
        (211.0, 156.5),
        (207.778, 164.278),
        (200.0, 167.5),
        (192.222, 164.278),
        (189.0, 156.5),
    ]
    for start, end in zip(outline, (*outline[1:], outline[0]), strict=True):
        layout.add_line(start, end, dxfattribs={"layer": "Bolt"})
    layout.add_line((178.0, 150.0), (222.0, 150.0), dxfattribs={"layer": "Bolt"})
    layout.add_line((200.0, 128.0), (200.0, 172.0), dxfattribs={"layer": "Bolt"})
    layout.add_line((500.0, 400.0), center, dxfattribs={"layer": "BoltMark"})
    layout.add_line((500.0, 400.0), (700.0, 400.0), dxfattribs={"layer": "BoltMark"})
    layout.add_text(
        "1*D22(22x35)",
        dxfattribs={"layer": "BoltMark", "insert": (510.0, 420.0), "height": 12.0},
    )

    graph = _graph(doc)
    model = extract_annotation_model(graph)
    nodes = {node.node_id: node for node in graph.nodes}
    label_edges = graph.edges_of(DrawingEdgeKind.LABELS)

    assert len(model.bolt_marks) == 1
    assert model.bolt_marks[0].target_source_ids
    assert model.bolt_marks[0].target_region_ids
    target = next(edge.target for edge in label_edges if edge.source == model.bolt_marks[0].block_handle)
    assert nodes[target].attributes["semantic_role"] == "physical_cut"
    assert nodes[target].attributes["geometry_kind"] == "polygonal_opening"
    assert nodes[target].attributes["nominal_diameter_mm"] == 22.0


def test_exploded_dimension_requires_extension_geometry_not_proximity_alone() -> None:
    doc = _base_document()
    layout = doc.modelspace()
    attrs = {"layer": "Z-DIMENSIONS"}
    layout.add_line((0, 400), (1000, 400), dxfattribs=attrs)
    layout.add_line((0, 0), (0, 420), dxfattribs=attrs)
    layout.add_line((1000, 0), (1000, 420), dxfattribs=attrs)
    layout.add_point((0, 400), dxfattribs=attrs)
    layout.add_point((1000, 400), dxfattribs=attrs)
    layout.add_text(
        "1000",
        dxfattribs={**attrs, "insert": (480, 420), "height": 12.0},
    )
    layout.add_text(
        "99999",
        dxfattribs={**attrs, "insert": (480, 520), "height": 12.0},
    )

    graph = _graph(doc)
    dimensions = [
        node
        for node in graph.nodes_of(DrawingNodeKind.DIMENSION)
        if node.attributes["representation"] == "exploded"
    ]

    assert len(dimensions) == 1
    assert dimensions[0].attributes["value_mm"] == 1000.0
    assert dimensions[0].attributes["text"] == "1000"
    assert any(
        edge.source == dimensions[0].node_id
        and edge.strength == AssociationStrength.GEOMETRIC
        for edge in graph.edges_of(DrawingEdgeKind.MEASURES)
    )


def test_numeric_text_and_equal_line_without_extension_chain_remain_unbound() -> None:
    doc = _base_document()
    _add_exploded_dimension(doc, include_extensions=False)

    graph = _graph(doc)

    assert graph.nodes_of(DrawingNodeKind.DIMENSION) == []
    assert graph.edges_of(DrawingEdgeKind.MEASURES) == []


def test_exploded_dimension_accepts_tekla_origin_gap_scaled_by_text_height() -> None:
    doc = _base_document()
    layout = doc.modelspace()
    attrs = {"layer": "Z-DIMENSIONS"}
    layout.add_line((0, 400), (1000, 400), dxfattribs=attrs)
    # Tekla leaves a paper-space origin offset between the measured object and
    # the extension line.  At this drawing scale the 1 mm paper offset becomes
    # 30 model millimetres for 90 mm dimension text.
    layout.add_line((0, 330), (0, 420), dxfattribs=attrs)
    layout.add_line((1000, 330), (1000, 420), dxfattribs=attrs)
    layout.add_text(
        "1000",
        dxfattribs={**attrs, "insert": (480, 420), "height": 90.0},
    )

    graph = _graph(doc)
    dimensions = graph.nodes_of(DrawingNodeKind.DIMENSION)

    assert len(dimensions) == 1
    assert dimensions[0].attributes["value_mm"] == 1000.0
    assert dimensions[0].attributes["origin_gap_mm"] == 30.0
    assert len(graph.edges_of(DrawingEdgeKind.MEASURES)) == 1


def test_exploded_partial_dimension_is_typed_but_not_called_a_view_extent() -> None:
    doc = _base_document()
    layout = doc.modelspace()
    attrs = {"layer": "Z-DIMENSIONS"}
    layout.add_line((100, 400), (350, 400), dxfattribs=attrs)
    layout.add_line((100, 300), (100, 420), dxfattribs=attrs)
    layout.add_line((350, 300), (350, 420), dxfattribs=attrs)
    layout.add_text(
        "250",
        dxfattribs={**attrs, "insert": (205, 420), "height": 30.0},
    )

    graph = _graph(doc)
    dimensions = graph.nodes_of(DrawingNodeKind.DIMENSION)

    assert len(dimensions) == 1
    assert dimensions[0].attributes["value_mm"] == 250.0
    assert dimensions[0].attributes["scope"] == "partial_or_untyped"
    assert dimensions[0].attributes["property_type"] == "unresolved"
    assert len(graph.edges_of(DrawingEdgeKind.MEASURES)) == 1


def test_horizontal_dimension_equal_to_view_height_is_not_a_longitudinal_extent() -> None:
    doc = _base_document()
    layout = doc.modelspace()
    attrs = {"layer": "Z-DIMENSIONS"}
    layout.add_line((100, 400), (400, 400), dxfattribs=attrs)
    layout.add_line((100, 300), (100, 420), dxfattribs=attrs)
    layout.add_line((400, 300), (400, 420), dxfattribs=attrs)
    layout.add_text(
        "300",
        dxfattribs={**attrs, "insert": (225, 420), "height": 30.0},
    )

    graph = _graph(doc)
    dimension = graph.nodes_of(DrawingNodeKind.DIMENSION)[0]
    measurement = graph.edges_of(DrawingEdgeKind.MEASURES)[0]

    assert dimension.attributes["orientation"] == "horizontal"
    assert dimension.attributes["scope"] == "partial_or_untyped"
    assert measurement.attributes["property_type"] == "unresolved"


def test_exploded_pitch_chain_preserves_interval_count_and_pitch() -> None:
    doc = _base_document()
    layout = doc.modelspace()
    attrs = {"layer": "Z-DIMENSIONS"}
    layout.add_line((100, 400), (200, 400), dxfattribs=attrs)
    layout.add_line((100, 300), (100, 420), dxfattribs=attrs)
    layout.add_line((200, 300), (200, 420), dxfattribs=attrs)
    layout.add_text(
        "4x100",
        dxfattribs={**attrs, "insert": (120, 420), "height": 30.0},
    )

    graph = _graph(doc)
    dimensions = graph.nodes_of(DrawingNodeKind.DIMENSION)
    model = extract_annotation_model(graph)

    assert len(dimensions) == 1
    assert dimensions[0].attributes["value_mm"] is None
    assert dimensions[0].attributes["chain_interval_count"] == 4
    assert dimensions[0].attributes["chain_pitch_mm"] == 100.0
    assert dimensions[0].attributes["scope"] == "pitch_chain"
    assert model.dimensions[0].value is None
    assert model.dimensions[0].chain_count == 4
    assert model.dimensions[0].chain_pitch == 100.0


def test_pitch_pattern_callout_binds_one_sided_leader_to_view() -> None:
    doc = _base_document()
    layout = doc.modelspace()
    attrs = {"layer": "Z-DIMENSIONS"}
    # Tekla's repeated-pitch callout is not always a two-extension dimension:
    # the pitch-sized stroke carries orientation and a one-sided leader reaches
    # the represented hole pattern.
    layout.add_line((100, 400), (100, 500), dxfattribs=attrs)
    layout.add_line((100, 400), (200, 300), dxfattribs=attrs)
    layout.add_line((100, 400), (120, 420), dxfattribs=attrs)
    layout.add_text(
        "4x100",
        dxfattribs={**attrs, "insert": (120, 470), "height": 30.0},
    )

    graph = _graph(doc)
    dimensions = graph.nodes_of(DrawingNodeKind.DIMENSION)
    edges = graph.edges_of(DrawingEdgeKind.MEASURES)

    assert len(dimensions) == 1
    assert dimensions[0].attributes["scope"] == "pitch_chain"
    assert dimensions[0].attributes["chain_interval_count"] == 4
    assert len(edges) == 1
    assert edges[0].rule_id == "TEKLA.DIMENSION.PATTERN_CALLOUT"


def test_arrowheads_without_object_referencing_extensions_remain_unbound() -> None:
    doc = _base_document()
    layout = doc.modelspace()
    attrs = {"layer": "Z-DIMENSIONS"}
    layout.add_line((0, 400), (1000, 400), dxfattribs=attrs)
    layout.add_line((0, 400), (20, 380), dxfattribs=attrs)
    layout.add_line((1000, 400), (980, 380), dxfattribs=attrs)
    layout.add_text(
        "1000",
        dxfattribs={**attrs, "insert": (480, 420), "height": 30.0},
    )

    graph = _graph(doc)

    assert graph.nodes_of(DrawingNodeKind.DIMENSION) == []
    assert graph.edges_of(DrawingEdgeKind.MEASURES) == []


def test_duplicate_equal_dimension_lines_have_a_stable_total_order() -> None:
    doc = _base_document()
    _add_exploded_dimension(doc, duplicate_dimension_line=True)

    graph = _graph(doc)

    assert len(graph.nodes_of(DrawingNodeKind.DIMENSION)) == 1
    assert len(graph.edges_of(DrawingEdgeKind.MEASURES)) == 1


def test_association_semantics_ignore_entity_order_and_xy_mirrors() -> None:
    forward = _base_document()
    _add_exploded_dimension(forward)
    reversed_order = _base_document()
    _add_exploded_dimension(reversed_order, reverse_entity_order=True)

    signatures = [
        _measurement_semantic_signature(_graph(document))
        for document in (
            forward,
            reversed_order,
            transform_modelspace(forward, Matrix44.scale(-1.0, 1.0, 1.0)),
            transform_modelspace(forward, Matrix44.scale(1.0, -1.0, 1.0)),
        )
    ]

    assert signatures[0]
    assert all(signature == signatures[0] for signature in signatures[1:])


def test_rotated_metadata_row_excludes_nearby_unaligned_large_number() -> None:
    doc = _base_document()
    origin = (200.0, 800.0)
    angle = 30.0
    direction = (cos(radians(angle)), sin(radians(angle)))
    normal = (-direction[1], direction[0])
    tokens = (
        ("BH-ROW-1", 0.0),
        ("BH300*200*8*12", 180.0),
        ("1000", 430.0),
        ("Q355B", 530.0),
        ("1:20", 620.0),
    )
    for value, offset in tokens:
        doc.modelspace().add_text(
            value,
            dxfattribs={
                "insert": (
                    origin[0] + direction[0] * offset,
                    origin[1] + direction[1] * offset,
                ),
                "height": 20.0,
                "rotation": angle,
            },
        )
    doc.modelspace().add_text(
        "99999",
        dxfattribs={
            "insert": (
                origin[0] + direction[0] * 420.0 + normal[0] * 35.0,
                origin[1] + direction[1] * 420.0 + normal[1] * 35.0,
            ),
            "height": 20.0,
            "rotation": angle,
        },
    )

    graph = _graph(doc)
    rows = graph.nodes_of(DrawingNodeKind.METADATA_ROW)

    assert len(rows) == 1
    assert rows[0].attributes["part_number"] == "BH-ROW-1"
    assert rows[0].attributes["profile"] == "BH300*200*8*12"
    assert rows[0].attributes["nominal_length_mm"] == 1000.0
    assert rows[0].attributes["material"] == "Q355B"
    assert "99999" not in rows[0].attributes["tokens"]


def test_part_mark_and_section_symbol_are_typed_source_nodes() -> None:
    doc = _base_document()
    doc.modelspace().add_text(
        "BH-ROW-1",
        dxfattribs={"layer": "PartMark", "insert": (100, 500), "height": 20.0},
    )
    doc.modelspace().add_line(
        (500, -100),
        (500, 400),
        dxfattribs={"layer": "Section"},
    )
    doc.modelspace().add_line(
        (520, -100),
        (520, 400),
        dxfattribs={"layer": "Section"},
    )
    doc.modelspace().add_text(
        "A",
        dxfattribs={"layer": "Section", "insert": (480, -120), "height": 20.0},
    )
    doc.modelspace().add_text(
        "A",
        dxfattribs={"layer": "Section", "insert": (520, -120), "height": 20.0},
    )

    first = _graph(doc)
    second = _graph(doc)

    assert len(first.nodes_of(DrawingNodeKind.PART_MARK)) == 1
    assert len(first.nodes_of(DrawingNodeKind.SECTION_SYMBOL)) == 1
    section = first.nodes_of(DrawingNodeKind.SECTION_SYMBOL)[0]
    section_relations = [
        edge
        for edge in first.edges_of(DrawingEdgeKind.LABELS)
        if edge.source == section.node_id
    ]
    assert len(section_relations) == 1
    assert section_relations[0].rule_id == "TEKLA.SECTION.CUT_LINE_TO_VIEW"
    model = extract_annotation_model(first)
    assert len(model.section_marks) == 1
    assert model.section_marks[0].target_region_id is not None
    assert first.to_dict() == second.to_dict()
    assert all(node.node_id for node in first.nodes)
    assert all(edge.edge_id for edge in first.edges)


def test_bolt_mark_is_one_composite_node_and_labels_its_leader_cut() -> None:
    doc = _base_document()
    doc.layers.add("Bolt")
    doc.layers.add("BoltMark")
    layout = doc.modelspace()
    layout.add_circle((200, 100), 20, dxfattribs={"layer": "Bolt"})
    layout.add_line((600, 500), (200, 100), dxfattribs={"layer": "BoltMark"})
    layout.add_line((600, 500), (680, 500), dxfattribs={"layer": "BoltMark"})
    layout.add_text(
        "1-M40",
        dxfattribs={"layer": "BoltMark", "insert": (610, 520), "height": 20.0},
    )

    graph = _graph(doc)
    marks = graph.nodes_of(DrawingNodeKind.BOLT_MARK)

    assert len(marks) == 1
    assert len(marks[0].source_ids) == 3


def test_mif_encoded_bolt_mark_is_decoded_before_graph_semantic_parsing() -> None:
    doc = _base_document()
    doc.layers.add("Bolt")
    doc.layers.add("BoltMark")
    layout = doc.modelspace()
    layout.add_circle((200, 100), 11, dxfattribs={"layer": "Bolt"})
    layout.add_line((600, 500), (200, 100), dxfattribs={"layer": "BoltMark"})
    layout.add_line((600, 500), (680, 500), dxfattribs={"layer": "BoltMark"})
    layout.add_text(
        r"16\M+5A6B522",
        dxfattribs={"layer": "BoltMark", "insert": (610, 520), "height": 20.0},
    )

    graph = _graph(doc)
    marks = graph.nodes_of(DrawingNodeKind.BOLT_MARK)
    model = extract_annotation_model(graph)

    assert len(marks) == 1
    assert marks[0].attributes["text"] == "16Φ22"
    assert len(model.bolt_marks) == 1
    assert model.bolt_marks[0].count == 16
    assert model.bolt_marks[0].diameter == 22.0
    assert model.bolt_marks[0].target_source_ids


def test_unresolved_mif_bolt_mark_remains_auditable_in_the_graph_model() -> None:
    doc = _base_document()
    doc.layers.add("Bolt")
    doc.layers.add("BoltMark")
    layout = doc.modelspace()
    layout.add_circle((200, 100), 11, dxfattribs={"layer": "Bolt"})
    layout.add_line((600, 500), (200, 100), dxfattribs={"layer": "BoltMark"})
    layout.add_line((600, 500), (680, 500), dxfattribs={"layer": "BoltMark"})
    layout.add_text(
        r"16\M+9A6B522",
        dxfattribs={"layer": "BoltMark", "insert": (610, 520), "height": 20.0},
    )

    model = extract_annotation_model(_graph(doc))

    assert model.bolt_marks == []
    assert len(model.unresolved_bolt_marks) == 1
    assert model.unresolved_bolt_marks[0].raw_text == r"16\M+9A6B522"
    assert model.unresolved_bolt_marks[0].target_source_ids


def test_ungrouped_bolt_marks_are_recovered_as_separate_connected_objects() -> None:
    doc = _base_document()
    doc.layers.add("Bolt")
    doc.layers.add("BoltMark")
    layout = doc.modelspace()
    for center, label_x in (((200.0, 100.0), 600.0), ((800.0, 100.0), 900.0)):
        layout.add_circle(center, 20.0, dxfattribs={"layer": "Bolt"})
        layout.add_line(
            (label_x, 500.0),
            center,
            dxfattribs={"layer": "BoltMark"},
        )
        layout.add_line(
            (label_x, 500.0),
            (label_x + 80.0, 500.0),
            dxfattribs={"layer": "BoltMark"},
        )
        layout.add_text(
            "1-M40",
            dxfattribs={
                "layer": "BoltMark",
                "insert": (label_x + 10.0, 520.0),
                "height": 20.0,
            },
        )

    graph = _graph(doc)
    model = extract_annotation_model(graph)
    marks = graph.nodes_of(DrawingNodeKind.BOLT_MARK)
    nodes = {node.node_id: node for node in graph.nodes}

    assert len(marks) == 2
    assert len(model.bolt_marks) == 2
    assert all(len(item.source_ids) == 3 for item in model.bolt_marks)
    assert len({item.target_source_ids for item in model.bolt_marks}) == 2
    assert all(mark.attributes["text"] == "1-M40" for mark in marks)
    assert all(
        any(
            edge.source == mark.node_id
            and nodes[edge.target].attributes["semantic_role"] == "physical_cut"
            for edge in graph.edges_of(DrawingEdgeKind.LABELS)
        )
        for mark in marks
    )


def test_annotation_model_contains_only_graph_bound_dimensions() -> None:
    doc = _base_document()
    attrs = {"layer": "Z-DIMENSIONS"}
    layout = doc.modelspace()
    layout.add_line((0, 400), (1000, 400), dxfattribs=attrs)
    layout.add_line((0, 0), (0, 420), dxfattribs=attrs)
    layout.add_line((1000, 0), (1000, 420), dxfattribs=attrs)
    layout.add_text(
        "1000",
        dxfattribs={**attrs, "insert": (480, 420), "height": 12.0},
    )
    layout.add_text(
        "99999",
        dxfattribs={**attrs, "insert": (480, 520), "height": 12.0},
    )

    model = extract_annotation_model(_graph(doc))

    assert [item.value for item in model.dimensions] == [1000.0]
    assert model.dimensions[0].target_node_id is not None
    assert model.dimensions[0].association_edge_ids
    assert model.dimensions[0].source_ids


def test_metadata_parser_consumes_graph_row_evidence() -> None:
    doc = _base_document()
    for value, x in (
        ("BH-ROW-1", 100.0),
        ("BH300*200*8*12", 300.0),
        ("1000", 600.0),
        ("Q355B", 720.0),
        ("1:20", 820.0),
    ):
        doc.modelspace().add_text(
            value,
            dxfattribs={"insert": (x, 800.0), "height": 20.0},
        )
    doc.modelspace().add_text(
        "99999",
        dxfattribs={"insert": (590.0, 850.0), "height": 20.0},
    )
    doc.modelspace().add_text(
        "GENERAL NOTE",
        dxfattribs={"insert": (1200.0, 800.0), "height": 20.0},
    )
    source = decode_source_document(doc)
    frame = infer_member_frames(source).selected
    regions = build_view_regions(source, frame)
    graph = build_drawing_graph(source, regions, frame)
    ir = materialize_lowering_ir(source, regions, frame)

    result = parse_bh_metadata_ir(ir, drawing_graph=graph)

    assert result.metadata.part_number == "BH-ROW-1"
    assert result.metadata.profile.raw_text == "BH300*200*8*12"
    assert result.metadata.nominal_length == 1000.0
    assert result.metadata.material == "Q355B"
    assert result.metadata.drawing_scale == 20.0
    assert [token.normalized for token in result.row_tokens] == [
        "BH-ROW-1",
        "BH300*200*8*12",
        "1000",
        "Q355B",
        "1:20",
    ]
