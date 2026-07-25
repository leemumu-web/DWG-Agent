from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.math import Matrix44

from steel_dxf_split.bh_frames import infer_member_frames
from steel_dxf_split.bh_regions import (
    build_view_regions,
    materialize_lowering_ir,
)
from steel_dxf_split.bh_semantics import part_blocks_from_ir
from steel_dxf_split.bh_source import (
    PrimitiveGeometry,
    decode_source_document,
    primitive_geometry_points,
)
from steel_dxf_split.geometry_types import BoundingBox

from bh_transform_fixtures import explode_top_level_inserts, transform_modelspace


PAIR_DIR = Path(__file__).resolve().parents[2] / "samples" / "bh_pairs"


def test_partial_arc_bbox_uses_swept_extrema_not_full_circle_envelope() -> None:
    geometry = PrimitiveGeometry(
        "ARC",
        coordinates=((10.0, 0.0), (0.0, 10.0)),
        center=(0.0, 0.0),
        radius=10.0,
        start_angle=0.0,
        end_angle=90.0,
    )

    bbox = BoundingBox.from_points(primitive_geometry_points(geometry))

    assert bbox == BoundingBox(0.0, 0.0, 10.0, 10.0)


def _two_view_document():
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.layers.add("Part")
    web = doc.blocks.new("WEB_VIEW")
    web.add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 300), (0, 300)],
        close=True,
        dxfattribs={"layer": "Part"},
    )
    flange = doc.blocks.new("FLANGE_VIEW")
    flange.add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 200), (0, 200)],
        close=True,
        dxfattribs={"layer": "Part"},
    )
    doc.modelspace().add_blockref("WEB_VIEW", (100, 100))
    doc.modelspace().add_blockref("FLANGE_VIEW", (100, 1000))
    return doc


def _region_signatures(
    doc,
    *,
    horizontal_axis_fact: bool = True,
) -> tuple[str, ...]:
    source = decode_source_document(doc)
    frame = infer_member_frames(
        source,
        horizontal_axis_fact=horizontal_axis_fact,
    ).selected
    result = build_view_regions(source, frame)
    return tuple(sorted(region.geometry_signature for region in result.part_views))


def test_explicit_blocks_and_exploded_entities_build_equivalent_view_regions() -> None:
    explicit = _two_view_document()
    exploded = explode_top_level_inserts(explicit)

    assert _region_signatures(explicit) == _region_signatures(exploded)
    assert len(_region_signatures(explicit)) == 2


def test_nested_parent_insert_keeps_two_semantic_views() -> None:
    source_doc = _two_view_document()
    nested = ezdxf.new()
    nested.header["$INSUNITS"] = 4
    nested.layers.add("Part")
    for name in ("WEB_VIEW", "FLANGE_VIEW"):
        source_block = source_doc.blocks[name]
        target = nested.blocks.new(name)
        for entity in source_block:
            target.add_entity(entity.copy())
    parent = nested.blocks.new("PARENT")
    parent.add_blockref("WEB_VIEW", (100, 100))
    parent.add_blockref("FLANGE_VIEW", (100, 1000))
    nested.modelspace().add_blockref(
        "PARENT",
        (2500, -300),
        dxfattribs={"rotation": 90.0},
    )

    assert _region_signatures(
        source_doc,
        horizontal_axis_fact=False,
    ) == _region_signatures(nested, horizontal_axis_fact=False)
    assert len(_region_signatures(nested, horizontal_axis_fact=False)) == 2


def test_repeated_nested_block_instances_remain_distinct_views() -> None:
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.layers.add("Part")
    view = doc.blocks.new("VIEW")
    view.add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 200), (0, 200)],
        close=True,
        dxfattribs={"layer": "Part"},
    )
    parent = doc.blocks.new("PARENT")
    parent.add_blockref("VIEW", (0, 0))
    parent.add_blockref("VIEW", (0, 1000))
    doc.modelspace().add_blockref("PARENT", (250, -400))

    signatures = _region_signatures(doc)

    assert len(signatures) == 2
    assert len(set(signatures)) == 1


def test_region_geometry_is_member_local_and_source_traceable() -> None:
    source = decode_source_document(_two_view_document())
    frame = infer_member_frames(source).selected

    result = build_view_regions(source, frame)

    assert len(result.part_views) == 2
    for region in result.part_views:
        assert region.bbox.min_x >= -1e-6
        assert region.bbox.min_y >= -1e-6
        assert region.source_ids
        assert set(region.source_ids).issubset(
            {entity.source_id for entity in source.entities}
        )
        assert region.geometry_signature


def test_lowering_ir_is_materialized_from_regions_with_source_provenance() -> None:
    doc = _two_view_document()
    source = decode_source_document(doc)
    frame = infer_member_frames(source).selected
    regions = build_view_regions(source, frame)

    ir = materialize_lowering_ir(source, regions, frame)

    part_blocks = part_blocks_from_ir(ir)

    assert len(part_blocks) == 2
    assert not hasattr(ir, "source_document")
    assert {
        atom.source.source_id
        for atom in ir.entities
        if atom.semantic_layer.value == "part_edge"
    } == {
        entity.source_id
        for entity in source.entities
        if entity.semantic_hint.role.value == "part_edge"
    }
    assert [
        block.source_view.region_id
        for block in ir.blocks
        if block.source_view is not None
    ] == [region.region_id for region in regions.part_views]
    assert [block.source_view for block in part_blocks] == [
        block.source_view
        for block in ir.blocks
        if block.source_view is not None
    ]
    for block, region in zip(part_blocks, regions.part_views, strict=True):
        assert block.source_view is not None
        assert len(block.entity_source_ids) == len(block.entities)
        lowering_block = next(
            item
            for item in ir.blocks
            if item.source_view is not None
            and item.source_view.region_id == block.source_view.region_id
        )
        assert block.entity_source_ids == tuple(
            atom.source.stable_id
            for atom in lowering_block.entities
            if atom.semantic_layer.value == "part_edge"
            and atom.entity.dxftype() in {"LINE", "ARC"}
        )
        assert block.source_view.source_ids == region.source_ids
        assert block.source_view.container_ids == region.container_ids
        assert block.source_view.geometry_signature == region.geometry_signature


def test_whole_drawing_rotation_does_not_change_region_identity() -> None:
    doc = _two_view_document()
    for insert in doc.modelspace().query("INSERT"):
        insert.dxf.rotation += 37.0

    rotated_signatures = _region_signatures(doc, horizontal_axis_fact=False)
    baseline_signatures = _region_signatures(
        _two_view_document(),
        horizontal_axis_fact=False,
    )

    assert rotated_signatures == baseline_signatures


def test_translation_and_mirror_do_not_change_region_identity() -> None:
    baseline = _region_signatures(_two_view_document())
    translated = transform_modelspace(
        _two_view_document(),
        Matrix44.translate(8765.25, -4321.75, 0),
    )
    mirrored = transform_modelspace(
        _two_view_document(),
        Matrix44.scale(-1, 1, 1),
    )

    assert _region_signatures(translated) == baseline
    assert _region_signatures(mirrored) == baseline


def test_twenty_real_drawings_keep_view_identity_after_explode() -> None:
    sources = sorted(PAIR_DIR.glob("*_拆板前.dxf"))

    assert len(sources) == 20
    for path in sources:
        doc = ezdxf.readfile(path)
        exploded = explode_top_level_inserts(doc)

        assert _region_signatures(exploded) == _region_signatures(doc), path.name
        assert len(_region_signatures(doc)) == 2, path.name
