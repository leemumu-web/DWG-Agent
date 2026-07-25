from __future__ import annotations

import json

import ezdxf
from ezdxf.math import Matrix44

from steel_dxf_split.bh_dialect import DEFAULT_TEKLA_DIALECT
from steel_dxf_split.bh_frontend import build_bh_document_ir
from steel_dxf_split.bh_ir import SemanticLayer, VisibilityClass
from steel_dxf_split.bh_source import decode_source_document

from bh_transform_fixtures import transform_modelspace


def _line_coordinates(source) -> list[tuple[tuple[float, float], ...]]:
    return sorted(
        entity.geometry.coordinates
        for entity in source.entities
        if entity.entity_type == "LINE" and entity.geometry is not None
    )


def test_direct_modelspace_entity_is_a_first_class_source_fact() -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    doc.modelspace().add_line((1, 2), (3, 4), dxfattribs={"layer": "Part"})

    source = decode_source_document(doc, DEFAULT_TEKLA_DIALECT)

    assert len(source.entities) == 1
    entity = source.entities[0]
    assert entity.path.layout == "Model"
    assert entity.path.inserts == ()
    assert entity.geometry is not None
    assert entity.geometry.coordinates == ((1.0, 2.0), (3.0, 4.0))
    assert entity.semantic_hint.role == SemanticLayer.PART_EDGE
    assert source.containers[0].container_id == "modelspace:direct"
    json.dumps(source.to_dict(), ensure_ascii=False)


def test_dot2_part_edge_is_decoded_as_hidden_projection_source_fact() -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    doc.linetypes.add("DOT2", pattern=[0.5, 0.0, -0.5])
    doc.modelspace().add_line(
        (1, 2),
        (3, 4),
        dxfattribs={"layer": "Part", "linetype": "DOT2"},
    )

    entity = decode_source_document(doc, DEFAULT_TEKLA_DIALECT).entities[0]

    assert entity.semantic_hint.role == SemanticLayer.PART_EDGE
    assert entity.visibility == VisibilityClass.HIDDEN
    assert entity.linetype == "DOT2"


def test_nested_insert_records_world_geometry_transform_chain_and_path() -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    leaf = doc.blocks.new("LEAF")
    leaf.add_line((0, 0), (10, 0), dxfattribs={"layer": "Part"})
    parent = doc.blocks.new("PARENT")
    parent.add_blockref("LEAF", (5, 0))
    doc.modelspace().add_blockref(
        "PARENT",
        (100, 200),
        dxfattribs={"rotation": 90.0, "xscale": 2.0, "yscale": 2.0},
    )

    source = decode_source_document(doc, DEFAULT_TEKLA_DIALECT)

    assert _line_coordinates(source) == [((100.0, 210.0), (100.0, 230.0))]
    entity = source.entities[0]
    assert entity.path.inserts == ("PARENT", "LEAF")
    assert entity.path.instance_indices == (0, 0)
    assert len(entity.transform_chain) == 2
    assert entity.container_id.startswith("insert:")


def test_minsert_emits_each_physical_instance_with_unique_identity() -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    cell = doc.blocks.new("CELL")
    cell.add_line((0, 0), (1, 0), dxfattribs={"layer": "Part"})
    doc.modelspace().add_blockref(
        "CELL",
        (10, 20),
        dxfattribs={
            "column_count": 2,
            "column_spacing": 5.0,
            "row_count": 1,
        },
    )

    first = decode_source_document(doc, DEFAULT_TEKLA_DIALECT)
    second = decode_source_document(doc, DEFAULT_TEKLA_DIALECT)

    assert _line_coordinates(first) == [
        ((10.0, 20.0), (11.0, 20.0)),
        ((15.0, 20.0), (16.0, 20.0)),
    ]
    assert len({entity.source_id for entity in first.entities}) == 2
    assert [entity.source_id for entity in first.entities] == [
        entity.source_id for entity in second.entities
    ]
    assert {entity.path.instance_indices[0] for entity in first.entities} == {0, 1}


def test_source_document_and_lowering_blocks_are_distinct_frontend_products() -> None:
    doc = ezdxf.new()
    doc.layers.add("Part")
    block = doc.blocks.new("VIEW")
    block.add_line((0, 0), (100, 0), dxfattribs={"layer": "Part"})
    doc.modelspace().add_blockref("VIEW", (20, 30))

    source = decode_source_document(doc)
    ir = build_bh_document_ir(doc)

    assert len(source.entities) == 1
    assert len(ir.blocks) == 1
    assert not hasattr(ir, "source_document")
    payload = source.to_dict()
    assert "DXFEntity" not in repr(payload)
    json.dumps(payload, ensure_ascii=False)


def test_mirrored_text_is_decoded_in_world_coordinates_with_handedness() -> None:
    doc = ezdxf.new()
    doc.modelspace().add_text(
        "BH-1",
        dxfattribs={"insert": (20.0, 30.0), "height": 5.0, "rotation": 0.0},
    )

    original = decode_source_document(doc).entities[0]
    mirrored = decode_source_document(
        transform_modelspace(doc, Matrix44.scale(-1.0, 1.0, 1.0))
    ).entities[0]

    assert original.geometry is not None
    assert mirrored.geometry is not None
    assert original.geometry.coordinates == ((20.0, 30.0),)
    assert mirrored.geometry.coordinates == ((-20.0, 30.0),)
    assert original.text_rotation == 0.0
    assert mirrored.text_rotation == 180.0
    assert original.text_normal_z == 1.0
    assert mirrored.text_normal_z == -1.0
