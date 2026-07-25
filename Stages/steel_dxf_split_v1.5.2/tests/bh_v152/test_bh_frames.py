from __future__ import annotations

from math import radians

import ezdxf
from ezdxf.math import Matrix44

from steel_dxf_split.bh_frames import (
    canonical_frame_signature,
    infer_member_frames,
)
from steel_dxf_split.bh_passes import BHCompileContext, FrontendPass, NormalizeFramePass
from steel_dxf_split.bh_source import decode_source_document

from bh_transform_fixtures import explode_top_level_inserts, transform_modelspace


def _part_polygon(points: list[tuple[float, float]]):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.layers.add("Part")
    doc.modelspace().add_lwpolyline(
        points,
        close=True,
        dxfattribs={"layer": "Part"},
    )
    return doc


def test_member_frame_is_invariant_to_translation_rotation_and_reflection() -> None:
    original = _part_polygon(
        [(0, 0), (1000, 0), (1000, 200), (600, 200), (600, 300), (0, 300)]
    )
    variants = (
        original,
        transform_modelspace(original, Matrix44.translate(1200, -350, 0)),
        transform_modelspace(original, Matrix44.z_rotate(radians(90))),
        transform_modelspace(original, Matrix44.z_rotate(radians(37))),
        transform_modelspace(original, Matrix44.scale(-1, 1, 1)),
    )

    signatures = []
    for doc in variants:
        source = decode_source_document(doc)
        result = infer_member_frames(source, horizontal_axis_fact=False)
        assert result.unique
        signatures.append(canonical_frame_signature(source, result.selected))

    assert len(set(signatures)) == 1


def test_horizontal_axis_fact_resolves_square_axis_ambiguity() -> None:
    source = decode_source_document(
        _part_polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    )

    result = infer_member_frames(source)

    assert result.unique
    assert all(abs(item.longitudinal.y) <= 1e-9 for item in result.candidates)

    unrestricted = infer_member_frames(source, horizontal_axis_fact=False)
    assert not unrestricted.unique
    assert len(unrestricted.candidates) >= 2
    assert unrestricted.score_margin <= 1e-9


def test_production_frame_forces_horizontal_member_axis() -> None:
    rotated = transform_modelspace(
        _part_polygon([(0, 0), (1000, 0), (1000, 200), (0, 200)]),
        Matrix44.z_rotate(radians(90)),
    )
    source = decode_source_document(rotated)

    production = infer_member_frames(source)
    unrestricted = infer_member_frames(source, horizontal_axis_fact=False)

    assert abs(production.selected.longitudinal.y) <= 1e-9
    assert abs(unrestricted.selected.longitudinal.x) <= 1e-9


def test_readable_text_basis_survives_explode_and_recovers_mirror_handedness() -> None:
    original = _part_polygon(
        [(0, 0), (1000, 0), (1000, 200), (600, 200), (600, 300), (0, 300)]
    )
    original.modelspace().add_text(
        "BH-1",
        dxfattribs={"insert": (100.0, 500.0), "height": 5.0, "rotation": 0.0},
    )
    mirrored = transform_modelspace(original, Matrix44.scale(-1.0, 1.0, 1.0))

    original_source = decode_source_document(original)
    mirrored_source = decode_source_document(mirrored)
    original_frame = infer_member_frames(original_source).selected
    mirrored_frame = infer_member_frames(mirrored_source).selected

    assert original_frame.longitudinal.x == 1.0
    assert original_frame.transverse.y == 1.0
    assert mirrored_frame.longitudinal.x == -1.0
    assert mirrored_frame.transverse.y == 1.0
    assert canonical_frame_signature(
        original_source, original_frame
    ) == canonical_frame_signature(mirrored_source, mirrored_frame)


def test_frame_transform_is_reversible() -> None:
    source = decode_source_document(
        _part_polygon([(20, 30), (1020, 30), (1020, 330), (20, 330)])
    )
    frame = infer_member_frames(source).selected

    for point in ((20.0, 30.0), (1020.0, 330.0), (400.5, 120.25)):
        local = frame.to_local_xy(*point)
        world = frame.to_world(local)
        assert abs(world.x - point[0]) <= 1e-9
        assert abs(world.y - point[1]) <= 1e-9


def test_frame_reports_source_evidence_for_selected_axis() -> None:
    source = decode_source_document(
        _part_polygon([(0, 0), (800, 0), (800, 200), (0, 200)])
    )

    result = infer_member_frames(source)

    assert result.selected.evidence_ids
    assert set(result.selected.evidence_ids).issubset(
        {entity.source_id for entity in source.entities}
    )


def test_normalize_frame_pass_records_candidates_in_compile_context() -> None:
    context = BHCompileContext(
        doc=_part_polygon([(0, 0), (800, 0), (800, 200), (0, 200)]),
        source_path=None,
    )
    FrontendPass().run(context)

    stage = NormalizeFramePass().run(context)

    assert context.frame_result is not None
    assert context.region_result is not None
    assert context.frame_result.unique
    assert stage.name == "source.normalize_and_partition"
    assert stage.outputs["candidate_count"] == 1
    assert stage.outputs["horizontal_axis_fact"] is True
    assert stage.outputs["selected_signature"]
    assert stage.outputs["part_view_count"] == 1


def test_page_spacing_between_views_does_not_become_the_member_axis() -> None:
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.layers.add("Part")
    view = doc.blocks.new("LONG_VIEW")
    view.add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 200), (0, 200)],
        close=True,
        dxfattribs={"layer": "Part"},
    )
    doc.modelspace().add_blockref("LONG_VIEW", (0, 0))
    doc.modelspace().add_blockref("LONG_VIEW", (0, 10_000))

    result = infer_member_frames(decode_source_document(doc))

    assert abs(result.selected.longitudinal.x) >= 1.0 - 1e-9
    assert abs(result.selected.longitudinal.y) <= 1e-9


def test_exploded_views_keep_the_same_member_axis() -> None:
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 4
    doc.layers.add("Part")
    view = doc.blocks.new("LONG_VIEW")
    view.add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 200), (0, 200)],
        close=True,
        dxfattribs={"layer": "Part"},
    )
    doc.modelspace().add_blockref("LONG_VIEW", (0, 0))
    doc.modelspace().add_blockref("LONG_VIEW", (0, 10_000))

    result = infer_member_frames(
        decode_source_document(explode_top_level_inserts(doc))
    )

    assert abs(result.selected.longitudinal.x) >= 1.0 - 1e-9
    assert abs(result.selected.longitudinal.y) <= 1e-9
