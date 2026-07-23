from __future__ import annotations

from decimal import Decimal

from tools.compare_ground_truth import (
    _collapse_part_rows,
    _normalized_part_type,
    _part_comparison_key,
)


def test_ground_truth_part_type_normalizes_box_cover() -> None:
    assert _normalized_part_type("BOX盖") == "BOX翼"
    assert _normalized_part_type(None) == "板材"


def test_component_scoped_comparison_key_keeps_component_identity() -> None:
    row = {
        "导入构件编号": "C1",
        "导入零件号": "P1-BOX盖",
        "规格": 10,
        "宽度": 100,
        "下料长度": 1000,
        "材质": "Q355B",
        "类型": "BOX盖",
        "班组": "A",
    }

    assert _part_comparison_key(row) == (
        "C1", "P1-BOX翼", 10, 100, 1000, "Q355B", "BOX翼",
    )


def test_global_comparison_key_ignores_component_and_team_and_collapses_summary() -> None:
    rows = [
        {
            "导入构件编号": "C1",
            "导入零件号": "P1",
            "规格": 10,
            "宽度": 100,
            "下料长度": 1000,
            "材质": "Q355B",
            "类型": None,
            "班组": "A",
            "汇总": 2,
        },
        {
            "导入构件编号": "C2",
            "导入零件号": "P1",
            "规格": 10,
            "宽度": 100,
            "下料长度": 1000,
            "材质": "Q355B",
            "类型": "板材",
            "班组": "B",
            "汇总": 3,
        },
    ]

    assert _part_comparison_key(rows[0]) == _part_comparison_key(rows[1])
    assert list(_collapse_part_rows(rows).values()) == [Decimal("5")]
