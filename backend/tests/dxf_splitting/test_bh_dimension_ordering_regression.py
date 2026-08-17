from __future__ import annotations

from steel_dxf_split.bh_associations import (
    DrawingNode,
    DrawingNodeKind,
    _ordered_dimension_group,
)


def test_equal_pitch_candidates_are_ordered_without_comparing_nodes() -> None:
    """Equal numeric keys must not fall through to DrawingNode comparison."""

    first = DrawingNode("cut-a", DrawingNodeKind.PRIMITIVE, ())
    second = DrawingNode("cut-b", DrawingNodeKind.PRIMITIVE, ())
    anchor = DrawingNode("anchor", DrawingNodeKind.PRIMITIVE, ())

    result = _ordered_dimension_group(
        [(1, 0.0, second), (0, 0.0, anchor), (1, 0.0, first)]
    )

    assert [node.node_id for _, _, node in result] == [
        "anchor",
        "cut-a",
        "cut-b",
    ]
