from __future__ import annotations

from pathlib import Path

import pytest

from steel_dxf_split.box.metadata import resolve_box_metadata
from steel_dxf_split.box.source_ir import (
    ObjectGroupIR,
    SourceDocumentIR,
    SourceEntityIR,
    build_source_ir,
)
from steel_dxf_split.box.view_frame import PartViewIR, ViewFrame, build_part_views
from steel_dxf_split.box.view_preprocessing import enumerate_role_view_variants
from steel_dxf_split.box.view_solver import (
    AmbiguousViewAssignmentError,
    _part_mark_h_view_target,
    enumerate_view_assignments,
    resolve_unique_view_assignment,
)
from tests.box_v1.paths import INPUTS


def _synthetic_view(
    *,
    longitudinal_span: float,
    transverse_span: float,
) -> PartViewIR:
    return PartViewIR(
        group_id="insert:synthetic",
        block_name="*SYNTHETIC",
        entities=(),
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=longitudinal_span,
            transverse_min=0.0,
            transverse_max=transverse_span,
        ),
    )


def test_role_view_variants_keep_ordinary_member_axis_unchanged() -> None:
    view = _synthetic_view(longitudinal_span=1000.0, transverse_span=300.0)

    assert enumerate_role_view_variants(
        view,
        nominal_length_mm=1000.0,
        transverse_mm=300.0,
    ) == (view,)


def test_role_view_variants_add_swapped_axis_only_with_two_dimension_evidence() -> None:
    view = _synthetic_view(longitudinal_span=700.0, transverse_span=700.0)

    variants = enumerate_role_view_variants(
        view,
        nominal_length_mm=700.0,
        transverse_mm=700.0,
    )

    assert len(variants) == 2
    assert variants[0] is view
    assert variants[1].frame.longitudinal_axis == (0.0, 1.0)
    assert variants[1].frame.transverse_axis == (-1.0, 0.0)


@pytest.mark.parametrize(
    ("member", "h_span", "b_span"),
    [
        ("2b1-cb-86", 750.0, 850.0),
        ("2b2-cb-145", 1800.0, 800.0),
        ("h-9-cb-69", 300.0, 1000.0),
        ("h-9-cb-94", 300.0, 930.0),
    ],
)
def test_non_square_sections_rank_the_correct_orthogonal_views(
    member: str,
    h_span: float,
    b_span: float,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    candidates = enumerate_view_assignments(
        build_part_views(source),
        resolve_box_metadata(source),
    )
    best = candidates[0]

    assert best.h_view.frame.transverse_span == pytest.approx(h_span, abs=0.01)
    assert best.b_view.frame.transverse_span == pytest.approx(b_span, abs=0.01)
    assert best.score < candidates[1].score
    assert resolve_unique_view_assignment(candidates) == best


def test_cranked_h_view_is_not_rejected_by_global_height_span() -> None:
    source = build_source_ir(INPUTS / "h-4-cb-37_拆板前.dxf")
    best = enumerate_view_assignments(
        build_part_views(source),
        resolve_box_metadata(source),
    )[0]

    assert best.b_view.frame.transverse_span == pytest.approx(300.0, abs=0.01)
    assert best.h_view.frame.transverse_span > 800.0
    assert best.h_view.group_id != best.b_view.group_id


@pytest.mark.parametrize("member", ["2b1-cb-56", "3t2-cb-117", "h-3-cb-2"])
def test_square_sections_keep_both_assignments_for_assembly_resolution(
    member: str,
) -> None:
    source = build_source_ir(INPUTS / f"{member}_拆板前.dxf")
    candidates = enumerate_view_assignments(
        build_part_views(source),
        resolve_box_metadata(source),
    )

    assert len(candidates) == 2
    assert candidates[0].score == pytest.approx(candidates[1].score, abs=1e-8)
    with pytest.raises(AmbiguousViewAssignmentError):
        resolve_unique_view_assignment(candidates)


def test_part_mark_leader_proves_h_view_for_square_box() -> None:
    source = build_source_ir(INPUTS / "h-3-cb-2_拆板前.dxf")
    candidates = enumerate_view_assignments(
        build_part_views(source),
        resolve_box_metadata(source),
        source=source,
    )

    assert candidates[0].h_view.group_id == "insert:AF"
    assert candidates[0].b_view.group_id == "insert:2AA"
    assert candidates[0].drawing_graph_score > candidates[1].drawing_graph_score
    assert candidates[0].drawing_graph_target_group_id == "insert:AF"
    assert candidates[0].drawing_graph_source_ids


def test_part_mark_leader_in_frame_blank_corner_does_not_target_view() -> None:
    triangle = (
        SourceEntityIR(
            source_id="part-a-bottom",
            group_id="insert:A",
            handle="part-a-bottom",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(0.0, 0.0),
            end=(100.0, 0.0),
        ),
        SourceEntityIR(
            source_id="part-a-diagonal",
            group_id="insert:A",
            handle="part-a-diagonal",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(100.0, 0.0),
            end=(0.0, 100.0),
        ),
        SourceEntityIR(
            source_id="part-a-left",
            group_id="insert:A",
            handle="part-a-left",
            kind="LINE",
            layer="Part",
            linetype="XKITLINE00",
            start=(0.0, 100.0),
            end=(0.0, 0.0),
        ),
    )
    view = PartViewIR(
        group_id="insert:A",
        block_name="*A",
        entities=triangle,
        frame=ViewFrame(
            origin=(0.0, 0.0),
            longitudinal_axis=(1.0, 0.0),
            transverse_axis=(0.0, 1.0),
            longitudinal_min=0.0,
            longitudinal_max=100.0,
            transverse_min=0.0,
            transverse_max=100.0,
        ),
    )
    part_mark_group = ObjectGroupIR(
        group_id="insert:mark",
        insert_handle="mark",
        block_name="*MARK",
        insert_point=(0.0, 0.0),
        rotation=0.0,
        scale=(1.0, 1.0, 1.0),
        source_ids=("mark-text", "mark-leader"),
        layers=("PartMark",),
    )
    source = SourceDocumentIR(
        path=Path("synthetic.dxf"),
        dxf_version="AC1027",
        units=4,
        declared_codepage="ANSI_1252",
        detected_encoding="ascii",
        file_sha256="synthetic",
        geometry_fingerprint="synthetic",
        groups=(part_mark_group,),
        entities=(
            *triangle,
            SourceEntityIR(
                source_id="mark-text",
                group_id="insert:mark",
                handle="mark-text",
                kind="TEXT",
                layer="PartMark",
                linetype="BYLAYER",
                text_raw="P1",
                text_decoded="P1",
            ),
            SourceEntityIR(
                source_id="mark-leader",
                group_id="insert:mark",
                handle="mark-leader",
                kind="LINE",
                layer="PartMark",
                linetype="BYLAYER",
                start=(150.0, 150.0),
                end=(90.0, 90.0),
            ),
        ),
    )

    assert _part_mark_h_view_target(source, (view,), "P1") is None


def test_candidate_order_does_not_depend_on_part_group_enumeration() -> None:
    source = build_source_ir(INPUTS / "2b1-cb-86_拆板前.dxf")
    metadata = resolve_box_metadata(source)
    views = build_part_views(source)

    first = enumerate_view_assignments(views, metadata)
    second = enumerate_view_assignments(tuple(reversed(views)), metadata)

    assert tuple(candidate.signature for candidate in first) == tuple(
        candidate.signature for candidate in second
    )
